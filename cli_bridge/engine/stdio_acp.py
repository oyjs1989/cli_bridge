"""Stdio-based ACP Connector for iflow CLI.

StdioACPClient has been extracted to _stdio_client.py.
This module re-exports all protocol types for backward compatibility
and defines the StdioACPAdapter (session-management wrapper).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Coroutine
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from cli_bridge.config.loader import DEFAULT_TIMEOUT
from cli_bridge.engine._acp_protocol import (
    clip_text as _clip_text_fn,
)
from cli_bridge.engine._acp_protocol import (
    estimate_tokens as _estimate_tokens_fn,
)
from cli_bridge.engine._acp_protocol import (
    get_session_key as _get_session_key_fn,
)
from cli_bridge.engine._acp_protocol import (
    inject_history_before_user_message as _inject_history_fn,
)
from cli_bridge.engine._acp_protocol import (
    is_context_overflow_error as _is_context_overflow_error_fn,
)

# Re-export all protocol types so existing importers keep working
from cli_bridge.engine._stdio_client import (  # noqa: F401
    ACPResponse,
    AgentMessageChunk,
    StdioACPClient,
    StdioACPConnectionError,
    StdioACPError,
    StdioACPTimeoutError,
    StopReason,
    ToolCall,
)


class StdioACPAdapter:
    """
    Stdio ACP 适配器 - 管理会话映射。
    """

    def __init__(
        self,
        iflow_path: str = "iflow",
        workspace: Path | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        default_model: str = "glm-5",
        thinking: bool = False,
        active_compress_trigger_tokens: int = 60000,
        mcp_proxy_port: int = 8888,
        mcp_servers_auto_discover: bool = True,
        mcp_servers_max: int = 10,
        mcp_servers_allowlist: list[str] | None = None,
        mcp_servers_blocklist: list[str] | None = None,
        mcp_servers_cached: list[dict] | None = None,
    ):
        self.iflow_path = iflow_path
        self.workspace = workspace
        self.timeout = timeout
        self.default_model = default_model
        self.thinking = thinking
        self.mcp_proxy_port = mcp_proxy_port
        self.mcp_servers_auto_discover = mcp_servers_auto_discover
        self.mcp_servers_max = mcp_servers_max
        self.mcp_servers_allowlist = mcp_servers_allowlist or []
        self.mcp_servers_blocklist = mcp_servers_blocklist or []
        self.mcp_servers_cached = mcp_servers_cached

        self._client: StdioACPClient | None = None
        self._session_map: dict[str, str] = {}
        self._loaded_sessions: set[str] = set()
        self._rehydrate_history: dict[str, str] = {}
        self._memory_constraints_cache: str | None = None
        self._active_compress_trigger_tokens = max(0, int(active_compress_trigger_tokens))
        self._active_compress_budget_tokens = 2200
        self._session_map_file = Path.home() / ".cli-bridge" / "session_mappings.json"
        self._session_lock = asyncio.Lock()
        self._load_session_map()

        logger.info(f"StdioACPAdapter: iflow_path={iflow_path}, workspace={workspace}")

    def _load_session_map(self) -> None:
        if self._session_map_file.exists():
            try:
                with open(self._session_map_file, encoding="utf-8") as f:
                    self._session_map = json.load(f)
                logger.debug(f"Loaded {len(self._session_map)} session mappings")
            except json.JSONDecodeError:
                logger.warning("Invalid session mapping file, starting fresh")
                self._session_map = {}

    def _save_session_map(self) -> None:
        self._session_map_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._session_map_file, "w", encoding="utf-8") as f:
            json.dump(self._session_map, f, indent=2, ensure_ascii=False)

    def _find_session_file(self, session_id: str) -> Path | None:
        sessions_dir = Path.home() / ".iflow" / "acp" / "sessions"

        if not sessions_dir.exists():
            return None

        session_file = sessions_dir / f"{session_id}.json"
        if session_file.exists():
            return session_file

        return None

    def _build_session_system_prompt(self) -> str | None:
        workspace = self.workspace or Path.cwd()
        agents_file = workspace / "AGENTS.md"
        if not agents_file.exists():
            return None

        try:
            agents_content = agents_file.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read AGENTS.md for system_prompt: {e}")
            return None

        return f"""[AGENTS - 工作空间指南]
以下是当前工作空间的行为指南，请严格遵循。

{agents_content}
[/AGENTS]

SOUL.md - Who You Are（你的灵魂）定义了你是谁，你的性格、特点、行为准则等核心信息。
IDENTITY.md - Your Identity（你的身份）定义了你的具体身份信息，如名字、年龄、职业、兴趣爱好等。
USERY.md - User Identity（用户身份）定义了用户的具体身份信息，如名字、年龄、职业、兴趣爱好等。
TOOLS.md - Your Tools（你的工具）定义了你可以使用的工具列表，包括每个工具的名称、功能描述、使用方法等, 每次学会一个工具，你便要主动更新该文件。"""

    def _extract_conversation_history(
        self,
        session_id: str,
        max_turns: int = 20,
        token_budget: int = 3000,
    ) -> str | None:
        import datetime

        session_file = self._find_session_file(session_id)
        if not session_file:
            logger.debug(f"Session file not found for: {session_id[:16]}...")
            return None

        try:
            with open(session_file, encoding="utf-8") as f:
                data = json.load(f)

            chat_history = data.get("chatHistory", [])
            if not chat_history:
                return None

            all_conversations: list[tuple[str, str, str]] = []
            for chat in chat_history:
                role = chat.get("role")
                parts = chat.get("parts", [])

                full_text = ""
                for part in parts:
                    if isinstance(part, dict):
                        text = part.get("text", "")
                        if text:
                            full_text += text + "\n"

                if not full_text.strip():
                    continue

                if role == "user":
                    if "用户消息:" in full_text:
                        idx = full_text.find("用户消息:") + len("用户消息:")
                        content = full_text[idx:].strip()
                    else:
                        continue

                    if len(content) < 2 or len(content) > 2000:
                        continue

                    timestamp = chat.get("timestamp") or data.get("createdAt", "")
                    time_str = ""
                    if timestamp:
                        try:
                            ts = timestamp.replace("Z", "+00:00")
                            dt = datetime.datetime.fromisoformat(ts.replace("+00:00", ""))
                            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                        except (ValueError, AttributeError):
                            pass
                    all_conversations.append(("user", time_str, content))

                elif role == "model":
                    content = full_text.strip()

                    if len(content) > 3000:
                        content = content[:3000] + "..."

                    if "<system-reminder>" in content or "[AGENTS - 工作空间指南]" in content:
                        continue

                    if len(content) > 10:
                        all_conversations.append(("assistant", "", content))

            if not all_conversations:
                return None

            history = self._build_budgeted_history_context(
                all_conversations,
                token_budget=token_budget,
                recent_turns=max_turns,
            )
            logger.info(
                f"Extracted {len(all_conversations)} conversation turns from session {session_id[:16]}..."
            )
            return history

        except Exception as e:
            logger.warning(f"Failed to extract conversation history: {e}")
            return None

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return _estimate_tokens_fn(text)

    @staticmethod
    def _clip_text(text: str, max_chars: int) -> str:
        return _clip_text_fn(text, max_chars)

    def _build_budgeted_history_context(
        self,
        conversations: list[tuple[str, str, str]],
        token_budget: int = 3000,
        recent_turns: int = 20,
    ) -> str:
        if not conversations:
            return "<history_context>\n</history_context>"

        def fmt(role: str, time_str: str, content: str, max_chars: int) -> str:
            body = self._clip_text(content, max_chars)
            if role == "user":
                return f"{time_str}\n用户：{body}".strip()
            return f"我：{body}"

        recent = conversations[-recent_turns:] if recent_turns > 0 else []
        older = conversations[:-len(recent)] if recent else conversations[:]

        summary_lines: list[str] = []
        if older:
            summary_lines.append("更早对话摘要：")
            for role, time_str, content in older[-40:]:
                prefix = "用户" if role == "user" else "我"
                item = self._clip_text(content, 120)
                if time_str:
                    summary_lines.append(f"- {time_str} {prefix}: {item}")
                else:
                    summary_lines.append(f"- {prefix}: {item}")

        blocks = [fmt(role, time_str, content, 1600) for role, time_str, content in recent]
        summary_block = "\n".join(summary_lines).strip()

        def build_text(cur_summary: str, cur_blocks: list[str]) -> str:
            parts: list[str] = []
            if cur_summary:
                parts.append(cur_summary)
            parts.extend([b for b in cur_blocks if b.strip()])
            return "<history_context>\n" + "\n\n".join(parts).strip() + "\n</history_context>"

        text = build_text(summary_block, blocks)

        # 优先丢弃最老的 recent blocks，确保最新上下文保留
        while self._estimate_tokens(text) > token_budget and len(blocks) > 4:
            blocks.pop(0)
            text = build_text(summary_block, blocks)

        # 再压缩摘要粒度
        if self._estimate_tokens(text) > token_budget and summary_block:
            compact_summary_lines = []
            for line in summary_lines[:24]:
                compact_summary_lines.append(self._clip_text(line, 90))
            summary_block = "\n".join(compact_summary_lines)
            text = build_text(summary_block, blocks)

        # 最后兜底裁剪
        while self._estimate_tokens(text) > token_budget and len(text) > 200:
            text = text[: max(200, int(len(text) * 0.9))].rstrip() + "\n</history_context>"

        return text

    async def connect(self) -> None:
        if self._client is None:
            self._client = StdioACPClient(
                iflow_path=self.iflow_path,
                workspace=self.workspace,
                timeout=self.timeout,
                mcp_proxy_port=self.mcp_proxy_port,
                mcp_servers_auto_discover=self.mcp_servers_auto_discover,
                mcp_servers_max=self.mcp_servers_max,
                mcp_servers_allowlist=self.mcp_servers_allowlist,
                mcp_servers_blocklist=self.mcp_servers_blocklist,
                mcp_servers_cached=self.mcp_servers_cached,
            )

        await self._client.start()
        await self._client.initialize()

        authenticated = await self._client.authenticate("iflow")
        if not authenticated:
            logger.warning("StdioACP authentication failed, some features may not work")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.stop()
            self._client = None

    def _get_session_key(self, channel: str, chat_id: str) -> str:
        return _get_session_key_fn(channel, chat_id)

    @staticmethod
    def _inject_history_before_user_message(message: str, history_context: str) -> str:
        return _inject_history_fn(message, history_context)

    @staticmethod
    def _extract_json_payload(text: str) -> dict[str, Any] | None:
        raw = (text or "").strip()
        if not raw:
            return None
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw).strip()
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            payload = json.loads(m.group(0))
            if isinstance(payload, dict):
                return payload
        except Exception:
            return None
        return None

    @staticmethod
    def _normalize_summary_items(items: Any, limit: int, fallback: str = "") -> list[str]:
        if not isinstance(items, list):
            return [fallback] if fallback else []
        out: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = " ".join(str(item or "").split())
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
            if len(out) >= limit:
                break
        if not out and fallback:
            out = [fallback]
        return out

    def _build_memory_summary_heuristic(self, text: str) -> dict[str, list[str]]:
        def clip(s: str, max_chars: int = 140) -> str:
            clean = " ".join((s or "").split())
            if len(clean) <= max_chars:
                return clean
            return clean[:max_chars].rstrip() + "..."

        user_msgs: list[str] = []
        assistant_msgs: list[str] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("更早对话摘要") or line.startswith("- "):
                continue
            m = re.match(r"^(用户|我)[：:]\s*(.+)$", line)
            if not m:
                continue
            who = m.group(1)
            body = clip(m.group(2))
            if who == "用户":
                user_msgs.append(body)
            else:
                assistant_msgs.append(body)

        unresolved_keywords = ("?", "？", "报错", "错误", "失败", "没", "未", "为什么", "怎么", "如何", "问题", "bug")
        solution_keywords = ("修复", "改为", "实现", "新增", "支持", "配置", "阈值", "压缩", "持久化", "重启", "降级", "回退", "优化")
        learning_keywords = ("通过", "使用", "采用", "机制", "策略", "约束", "预算", "滚动")
        resolved_keywords = ("已修复", "已解决", "已恢复", "已处理", "已完成", "已生效", "已支持")

        highlights = self._normalize_summary_items(user_msgs[-3:] + assistant_msgs[-2:], 4)
        unresolved = self._normalize_summary_items(
            [msg for msg in user_msgs if any(k in msg for k in unresolved_keywords)][-4:],
            3,
        )
        solutions = self._normalize_summary_items(
            [msg for msg in assistant_msgs if any(k in msg for k in solution_keywords)][-6:],
            4,
        )
        learnings = self._normalize_summary_items(
            [msg for msg in assistant_msgs if any(k in msg for k in learning_keywords)][-5:],
            3,
        )
        resolved = self._normalize_summary_items(
            [msg for msg in assistant_msgs if any(k in msg for k in resolved_keywords)][-5:],
            4,
        )
        return {
            "highlights": highlights,
            "unresolved": unresolved,
            "solutions": solutions,
            "learnings": learnings,
            "resolved": resolved,
        }

    async def _build_memory_summary_by_agent(
        self,
        channel: str,
        chat_id: str,
        reason: str,
        history_text: str,
    ) -> dict[str, list[str]] | None:
        if not self._client:
            return None
        try:
            sub_session_id = await self._client.create_session(
                workspace=self.workspace,
                model=self.default_model,
                approval_mode="yolo",
                system_prompt="你是会话记忆压缩助手。仅输出合法 JSON，不要输出解释。",
            )
            constraints = self._load_memory_constraints()
            prompt = (
                "请从下面对话摘要中提取结构化记忆。\n"
                "要求：\n"
                "1) highlights: 今日重点（<=4）\n"
                "2) unresolved: 未解决问题（<=4）\n"
                "3) solutions: 技术方案/结论（<=4）\n"
                "4) learnings: 新增学习（<=3）\n"
                "5) resolved: 本轮已解决的问题（<=4）\n"
                "输出 JSON 对象，键固定为 highlights/unresolved/solutions/learnings/resolved，值为字符串数组。\n"
                "如果某项为空，返回空数组。\n\n"
                f"session={channel}:{chat_id}\nreason={reason}\n\n"
                f"memory_constraints:\n{constraints or '(none)'}\n\n"
                f"{history_text}"
            )
            logger.debug(f"Memory summary sub-session created: {sub_session_id[:16]}... for {channel}:{chat_id}")
            response = await self._client.prompt(
                session_id=sub_session_id,
                message=prompt,
                timeout=min(self.timeout, 120),
            )
            if response.error:
                return None
            payload = self._extract_json_payload(response.content)
            if not payload:
                return None
            return {
                "highlights": self._normalize_summary_items(payload.get("highlights"), 4),
                "unresolved": self._normalize_summary_items(payload.get("unresolved"), 4),
                "solutions": self._normalize_summary_items(payload.get("solutions"), 4),
                "learnings": self._normalize_summary_items(payload.get("learnings"), 3),
                "resolved": self._normalize_summary_items(payload.get("resolved"), 4),
            }
        except Exception as e:
            logger.debug(f"Memory summary by agent failed, fallback to heuristic: {e}")
            return None

    async def _persist_compression_snapshot(
        self,
        channel: str,
        chat_id: str,
        reason: str,
        history_context: str,
        estimated_tokens: int = 0,
    ) -> None:
        text = (history_context or "").strip()
        if not text:
            return
        text = text.replace("<history_context>", "").replace("</history_context>", "").strip()
        if not text:
            return

        summary = await self._build_memory_summary_by_agent(channel, chat_id, reason, text)
        if not summary:
            summary = self._build_memory_summary_heuristic(text)

        highlights = self._normalize_summary_items(
            summary.get("highlights"),
            4,
            fallback="本轮触发压缩，但未提取到足够明确的重点语句。",
        )
        unresolved = self._normalize_summary_items(
            summary.get("unresolved"),
            4,
            fallback="暂无明确未解决问题（建议人工复核最新对话）。",
        )
        solutions = self._normalize_summary_items(
            summary.get("solutions"),
            4,
            fallback="本轮未检测到稳定技术方案结论。",
        )
        learnings = self._normalize_summary_items(
            summary.get("learnings"),
            3,
            fallback="本轮未检测到可沉淀的新技术要点。",
        )
        resolved_for_todo = self._normalize_summary_items(summary.get("resolved"), 4)

        unresolved_for_todo = [
            item
            for item in self._normalize_summary_items(summary.get("unresolved"), 4)
            if "暂无明确未解决问题" not in item
        ]

        workspace = self.workspace or Path.cwd()
        memory_dir = workspace / "memory"
        try:
            memory_dir.mkdir(parents=True, exist_ok=True)
            day_file = memory_dir / f"{datetime.now().strftime('%Y-%m-%d')}.md"
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            reason_map = {
                "active_session_rotate": "活跃会话过长，触发滚动压缩",
                "load_failed_rehydrate": "旧会话加载失败，触发历史重建",
                "invalid_request_recreate": "会话请求失效，触发重建",
                "invalid_request_recreate_stream": "流式会话请求失效，触发重建",
                "context_overflow_compact_retry": "上下文疑似超限，触发紧凑重试",
                "context_overflow_compact_retry_stream": "流式上下文疑似超限，触发紧凑重试",
            }
            reason_text = reason_map.get(reason, reason)
            highlights_block = "\n".join([f"- {x}" for x in highlights])
            unresolved_block = "\n".join([f"- {x}" for x in unresolved])
            solutions_block = "\n".join([f"- {x}" for x in solutions])
            learnings_block = "\n".join([f"- {x}" for x in learnings])
            entry = (
                f"\n\n## [{ts}] 会话压缩重点\n"
                f"- session: {channel}:{chat_id}\n"
                f"- reason: {reason_text}\n"
                f"- estimated_tokens: {estimated_tokens}\n\n"
                f"### 今日重点\n{highlights_block}\n\n"
                f"### 未解决问题\n{unresolved_block}\n\n"
                f"### 技术方案/结论\n{solutions_block}\n\n"
                f"### 新增学习\n{learnings_block}\n"
            )
            with open(day_file, "a", encoding="utf-8") as f:
                f.write(entry)
            self._sync_todo_items(
                memory_dir=memory_dir,
                channel=channel,
                chat_id=chat_id,
                unresolved_items=unresolved_for_todo,
                resolved_items=resolved_for_todo,
            )
            logger.info(
                f"Persisted compression memory summary to {day_file} for {channel}:{chat_id} ({reason})"
            )
        except Exception as e:
            logger.warning(f"Failed to persist compression snapshot: {e}")

    def _schedule_persist_compression_snapshot(
        self,
        channel: str,
        chat_id: str,
        reason: str,
        history_context: str,
        estimated_tokens: int = 0,
    ) -> None:
        async def _runner() -> None:
            # 延迟执行，优先让主会话 prompt 先走，避免回包抖动
            await asyncio.sleep(0.5)
            await self._persist_compression_snapshot(
                channel=channel,
                chat_id=chat_id,
                reason=reason,
                history_context=history_context,
                estimated_tokens=estimated_tokens,
            )

        task = asyncio.create_task(_runner())
        task.add_done_callback(
            lambda t: logger.warning(f"Background memory persistence failed: {t.exception()}")
            if t.exception()
            else None
        )

    def _sync_todo_items(
        self,
        memory_dir: Path,
        channel: str,
        chat_id: str,
        unresolved_items: list[str],
        resolved_items: list[str],
    ) -> None:
        if not unresolved_items and not resolved_items:
            return
        todo_file = memory_dir / "TODO.md"
        try:
            lines = todo_file.read_text(encoding="utf-8").splitlines() if todo_file.exists() else []
            if not lines:
                lines = ["# 未解决问题跟踪", ""]

            task_re = re.compile(r"^- \[( |x|X)\]\s+(.+)$")

            def norm(s: str) -> str:
                return " ".join((s or "").split()).strip().lower()

            def matched(a: str, b: str) -> bool:
                na, nb = norm(a), norm(b)
                if not na or not nb:
                    return False
                if na == nb:
                    return True
                if len(na) >= 6 and na in nb:
                    return True
                if len(nb) >= 6 and nb in na:
                    return True
                return False

            changed = False
            existing_tasks: list[tuple[int, bool, str]] = []
            for idx, line in enumerate(lines):
                m = task_re.match(line.strip())
                if not m:
                    continue
                done = m.group(1).lower() == "x"
                text = " ".join(m.group(2).split())
                existing_tasks.append((idx, done, text))

            # 自动勾选已完成
            for resolved in resolved_items:
                for idx, done, task_text in existing_tasks:
                    if done:
                        continue
                    if matched(resolved, task_text):
                        lines[idx] = f"- [x] {task_text}"
                        changed = True

            all_task_norm = {
                norm(task_text)
                for _, _, task_text in existing_tasks
                if task_text
            }
            new_items: list[str] = []
            for item in unresolved_items:
                clean = " ".join((item or "").split())
                if not clean:
                    continue
                if norm(clean) in all_task_norm:
                    continue
                new_items.append(clean)
                all_task_norm.add(norm(clean))

            if new_items:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if lines and lines[-1].strip():
                    lines.append("")
                lines.append(f"## {ts} ({channel}:{chat_id})")
                for item in new_items:
                    lines.append(f"- [ ] {item}")
                lines.append("")
                changed = True

            if changed:
                todo_file.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
                logger.info(
                    f"Updated TODO file: {todo_file} (+{len(new_items)} unresolved, {len(resolved_items)} resolved candidates)"
                )
        except Exception as e:
            logger.warning(f"Failed to sync TODO items: {e}")

    def _load_memory_constraints(self) -> str:
        if self._memory_constraints_cache is not None:
            return self._memory_constraints_cache
        workspace = self.workspace or Path.cwd()
        agents_file = workspace / "AGENTS.md"
        if not agents_file.exists():
            self._memory_constraints_cache = ""
            return ""
        try:
            content = agents_file.read_text(encoding="utf-8")
        except Exception:
            self._memory_constraints_cache = ""
            return ""
        marker = "## Memory"
        start = content.find(marker)
        if start < 0:
            self._memory_constraints_cache = ""
            return ""
        tail = content[start:]
        next_idx = tail.find("\n## ", len(marker))
        section = tail if next_idx < 0 else tail[:next_idx]
        lines = [line.strip() for line in section.splitlines() if line.strip()]
        keep: list[str] = []
        for line in lines:
            lower = line.lower()
            if "daily notes" in lower or "long-term" in lower:
                keep.append(line)
            if "only load in main session" in lower:
                keep.append(line)
            if "do not load in shared contexts" in lower:
                keep.append(line)
            if "security" in lower and "shared contexts" in lower:
                keep.append(line)
        self._memory_constraints_cache = "\n".join(keep[:8]) if keep else ""
        return self._memory_constraints_cache

    def _apply_compression_constraints(self, history_context: str, channel: str, chat_id: str) -> str:
        if not history_context:
            return history_context
        constraints = self._load_memory_constraints()
        if not constraints:
            return history_context
        is_group_like = False
        chat_text = str(chat_id or "")
        if channel == "telegram" and chat_text.startswith("-"):
            is_group_like = True
        if channel in {"discord", "slack"}:
            is_group_like = True
        security_note = (
            "共享会话安全约束：禁止注入或泄露 MEMORY.md 的个人长期记忆，只保留任务相关事实。"
            if is_group_like
            else "主会话约束：可参考 memory/MEMORY.md 的长期记忆，但仅用于任务连续性。"
        )
        return (
            "<memory_constraints>\n"
            f"{constraints}\n"
            f"{security_note}\n"
            "压缩要求：保留关键决策、用户偏好、未完成事项；优先保留最近信息，去除冗余。\n"
            "</memory_constraints>\n\n"
            f"{history_context}"
        )

    def _estimate_session_history_tokens(self, session_id: str) -> int:
        session_file = self._find_session_file(session_id)
        if not session_file:
            return 0
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
        except Exception:
            return 0
        chat_history = data.get("chatHistory") or []
        if not isinstance(chat_history, list):
            return 0
        total_chars = 0
        for chat in chat_history:
            if not isinstance(chat, dict):
                continue
            parts = chat.get("parts", [])
            if not isinstance(parts, list):
                continue
            for part in parts:
                if isinstance(part, dict):
                    total_chars += len(str(part.get("text", "")))
        return max(1, total_chars // 4)

    async def _maybe_compress_active_session(
        self,
        key: str,
        channel: str,
        chat_id: str,
        session_id: str,
        message: str,
        model: str | None,
    ) -> tuple[str, str]:
        estimated_tokens = self._estimate_session_history_tokens(session_id)
        if estimated_tokens < self._active_compress_trigger_tokens:
            return session_id, message

        logger.warning(
            f"Active session too large ({estimated_tokens} tokens est), rotating with compressed context: {key}"
        )
        history_context = self._extract_conversation_history(
            session_id,
            max_turns=40,
            token_budget=self._active_compress_budget_tokens,
        ) or ""
        if not history_context:
            return session_id, message

        self._schedule_persist_compression_snapshot(
            channel=channel,
            chat_id=chat_id,
            reason="active_session_rotate",
            history_context=history_context,
            estimated_tokens=estimated_tokens,
        )
        history_context = self._apply_compression_constraints(history_context, channel, chat_id)
        await self._invalidate_session(key)
        new_session_id = await self._create_new_session(key, model)
        new_message = self._inject_history_before_user_message(message, history_context)
        logger.info(f"Active session compressed and rotated for {key}")
        return new_session_id, new_message

    @staticmethod
    def _is_context_overflow_error(error_text: str) -> bool:
        return _is_context_overflow_error_fn(error_text)

    async def _get_or_create_session(
        self,
        channel: str,
        chat_id: str,
        model: str | None = None,
    ) -> str:
        key = self._get_session_key(channel, chat_id)

        if key in self._session_map:
            session_id = self._session_map[key]
            if session_id in self._loaded_sessions:
                logger.debug(f"Reusing existing session: {key} -> {session_id[:16]}...")
                return session_id

            if not self._client:
                raise StdioACPConnectionError("StdioACP client not connected")

            loaded = await self._client.load_session(session_id)
            if loaded:
                self._loaded_sessions.add(session_id)
                logger.info(f"Loaded existing session for {key}: {session_id[:16]}...")
                return session_id

            logger.warning(f"Failed to load mapped session for {key}, recreating")
            history_context = self._extract_conversation_history(session_id) or ""
            if history_context:
                self._schedule_persist_compression_snapshot(
                    channel=channel,
                    chat_id=chat_id,
                    reason="load_failed_rehydrate",
                    history_context=history_context,
                    estimated_tokens=self._estimate_tokens(history_context),
                )
                self._rehydrate_history[key] = history_context
                logger.info(f"Queued history rehydrate for {key} before first prompt")
            await self._invalidate_session(key)

        return await self._create_new_session(key, model)

    async def _create_new_session(
        self,
        key: str,
        model: str | None = None,
    ) -> str:
        async with self._session_lock:
            if key in self._session_map:
                return self._session_map[key]

            if not self._client:
                raise StdioACPConnectionError("StdioACP client not connected")

            system_prompt = self._build_session_system_prompt()
            session_id = await self._client.create_session(
                workspace=self.workspace,
                model=model or self.default_model,
                approval_mode="yolo",
                system_prompt=system_prompt,
            )

            self._session_map[key] = session_id
            self._loaded_sessions.add(session_id)
            self._save_session_map()
            logger.info(f"StdioACP session mapped: {key} -> {session_id[:16]}...")

            return session_id

    async def _invalidate_session(self, key: str) -> str | None:
        old_session = self._session_map.pop(key, None)
        if old_session:
            self._loaded_sessions.discard(old_session)
            self._save_session_map()
            logger.info(f"Session invalidated: {key} -> {old_session[:16]}...")
        return old_session

    async def chat(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: str | None = None,
        timeout: int | None = None,
    ) -> str:
        if not self._client:
            raise StdioACPConnectionError("StdioACP client not connected")

        key = self._get_session_key(channel, chat_id)
        session_id = await self._get_or_create_session(channel, chat_id, model)
        queued_history = self._rehydrate_history.pop(key, "")
        if queued_history:
            queued_history = self._apply_compression_constraints(queued_history, channel, chat_id)
            message = self._inject_history_before_user_message(message, queued_history)
            logger.info(f"Injected queued history for {key}")
        session_id, message = await self._maybe_compress_active_session(
            key, channel, chat_id, session_id, message, model
        )

        try:
            response = await self._client.prompt(
                session_id=session_id,
                message=message,
                timeout=timeout or self.timeout,
            )
        except StdioACPTimeoutError:
            logger.warning(f"Prompt timeout, cancel and recreate session: {key}")
            try:
                await self._client.cancel(session_id)
            except Exception as e:
                logger.debug(f"Failed to cancel timed-out session {session_id[:16]}...: {e}")

            await self._invalidate_session(key)
            session_id = await self._create_new_session(key, model)
            response = await self._client.prompt(
                session_id=session_id,
                message=message,
                timeout=timeout or self.timeout,
            )

        if response.error and "Invalid request" in response.error:
            logger.warning(f"Session invalid, recreating: {key}")
            old_session_id = await self._invalidate_session(key)
            history_context = ""
            if old_session_id:
                history_context = self._extract_conversation_history(old_session_id) or ""

            session_id = await self._create_new_session(key, model)

            if history_context:
                self._schedule_persist_compression_snapshot(
                    channel=channel,
                    chat_id=chat_id,
                    reason="invalid_request_recreate",
                    history_context=history_context,
                    estimated_tokens=self._estimate_tokens(history_context),
                )
                history_context = self._apply_compression_constraints(history_context, channel, chat_id)
                message = self._inject_history_before_user_message(message, history_context)
                logger.info("Injected conversation history before user message")

            response = await self._client.prompt(
                session_id=session_id,
                message=message,
                timeout=timeout or self.timeout,
            )

        if response.error and self._is_context_overflow_error(response.error):
            logger.warning(f"Context overflow suspected, recreating with compact history: {key}")
            old_session_id = await self._invalidate_session(key)
            history_context = ""
            if old_session_id:
                history_context = self._extract_conversation_history(
                    old_session_id,
                    max_turns=4,
                    token_budget=1200,
                ) or ""

            session_id = await self._create_new_session(key, model)

            retry_message = message
            if history_context:
                self._schedule_persist_compression_snapshot(
                    channel=channel,
                    chat_id=chat_id,
                    reason="context_overflow_compact_retry",
                    history_context=history_context,
                    estimated_tokens=self._estimate_tokens(history_context),
                )
                history_context = self._apply_compression_constraints(history_context, channel, chat_id)
                retry_message = self._inject_history_before_user_message(retry_message, history_context)
                logger.info("Injected compact conversation history before user message")

            response = await self._client.prompt(
                session_id=session_id,
                message=retry_message,
                timeout=timeout or self.timeout,
            )

        if response.error:
            if "terminated" in response.error.lower() and (response.content or "").strip():
                logger.warning("Chat returned terminated with content, returning partial content")
            else:
                raise StdioACPError(f"Chat error: {response.error}")

        if self.thinking and response.thought:
            return f"[Thinking]\n{response.thought}\n\n[Response]\n{response.content}"

        return response.content

    async def chat_stream(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: str | None = None,
        timeout: int | None = None,
        on_chunk: Callable[[AgentMessageChunk], Coroutine] | None = None,
        on_tool_call: Callable[[ToolCall], Coroutine] | None = None,
        on_event: Callable[[dict[str, Any]], Coroutine] | None = None,
    ) -> str:
        if not self._client:
            raise StdioACPConnectionError("StdioACP client not connected")

        key = self._get_session_key(channel, chat_id)
        session_id = await self._get_or_create_session(channel, chat_id, model)
        queued_history = self._rehydrate_history.pop(key, "")
        if queued_history:
            queued_history = self._apply_compression_constraints(queued_history, channel, chat_id)
            message = self._inject_history_before_user_message(message, queued_history)
            logger.info(f"Injected queued history for {key} (stream)")
        session_id, message = await self._maybe_compress_active_session(
            key, channel, chat_id, session_id, message, model
        )

        content_parts: list[str] = []

        async def handle_chunk(chunk: AgentMessageChunk):
            if not chunk.is_thought and chunk.text:
                content_parts.append(chunk.text)
            if on_chunk:
                result = on_chunk(chunk)
                if asyncio.iscoroutine(result):
                    await result

        async def handle_tool_call(tool_call: ToolCall):
            if on_tool_call:
                result = on_tool_call(tool_call)
                if asyncio.iscoroutine(result):
                    await result

        async def handle_event(event: dict[str, Any]):
            if on_event:
                result = on_event(event)
                if asyncio.iscoroutine(result):
                    await result

        try:
            response = await self._client.prompt(
                session_id=session_id,
                message=message,
                timeout=timeout or self.timeout,
                on_chunk=handle_chunk,
                on_tool_call=handle_tool_call,
                on_event=handle_event,
            )
        except StdioACPTimeoutError:
            logger.warning(f"Stream prompt timeout, cancel and recreate session: {key}")
            try:
                await self._client.cancel(session_id)
            except Exception as e:
                logger.debug(f"Failed to cancel timed-out session {session_id[:16]}...: {e}")

            await self._invalidate_session(key)
            session_id = await self._create_new_session(key, model)
            content_parts.clear()
            response = await self._client.prompt(
                session_id=session_id,
                message=message,
                timeout=timeout or self.timeout,
                on_chunk=handle_chunk,
                on_tool_call=handle_tool_call,
                on_event=handle_event,
            )

        if response.error and "Invalid request" in response.error:
            logger.warning(f"Session invalid (stream), recreating: {key}")
            old_session_id = await self._invalidate_session(key)
            history_context = ""
            if old_session_id:
                history_context = self._extract_conversation_history(old_session_id) or ""

            session_id = await self._create_new_session(key, model)

            if history_context:
                self._schedule_persist_compression_snapshot(
                    channel=channel,
                    chat_id=chat_id,
                    reason="invalid_request_recreate_stream",
                    history_context=history_context,
                    estimated_tokens=self._estimate_tokens(history_context),
                )
                history_context = self._apply_compression_constraints(history_context, channel, chat_id)
                message = self._inject_history_before_user_message(message, history_context)
                logger.info("Injected conversation history before user message (stream)")

            content_parts.clear()
            response = await self._client.prompt(
                session_id=session_id,
                message=message,
                timeout=timeout or self.timeout,
                on_chunk=handle_chunk,
                on_tool_call=handle_tool_call,
                on_event=handle_event,
            )

        stream_content = "".join(content_parts) or response.content

        should_recover = False
        if response.error and self._is_context_overflow_error(response.error):
            should_recover = True
            logger.warning(f"Context overflow suspected (stream), recreating with compact history: {key}")
        elif not (stream_content or "").strip():
            should_recover = True
            logger.warning(f"Empty stream response detected, recreating with compact history: {key}")

        if should_recover:
            old_session_id = await self._invalidate_session(key)
            history_context = ""
            if old_session_id:
                history_context = self._extract_conversation_history(
                    old_session_id,
                    max_turns=4,
                    token_budget=1200,
                ) or ""

            session_id = await self._create_new_session(key, model)

            retry_message = message
            if history_context:
                self._schedule_persist_compression_snapshot(
                    channel=channel,
                    chat_id=chat_id,
                    reason="context_overflow_compact_retry_stream",
                    history_context=history_context,
                    estimated_tokens=self._estimate_tokens(history_context),
                )
                history_context = self._apply_compression_constraints(history_context, channel, chat_id)
                retry_message = self._inject_history_before_user_message(retry_message, history_context)
                logger.info("Injected compact conversation history before user message (stream)")

            content_parts.clear()
            response = await self._client.prompt(
                session_id=session_id,
                message=retry_message,
                timeout=timeout or self.timeout,
                on_chunk=handle_chunk,
                on_tool_call=handle_tool_call,
                on_event=handle_event,
            )
            stream_content = "".join(content_parts) or response.content

        if response.error:
            if "terminated" in response.error.lower() and (stream_content or "").strip():
                logger.warning("Chat stream returned terminated with content, returning partial content")
            else:
                raise StdioACPError(f"Chat error: {response.error}")

        return stream_content

    async def new_chat(
        self,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        model: str | None = None,
        timeout: int | None = None,
    ) -> str:
        self.clear_session(channel, chat_id)
        return await self.chat(message, channel, chat_id, model, timeout)

    async def health_check(self) -> bool:
        if self._client is None:
            return False
        return await self._client.is_connected()

    def clear_session(self, channel: str, chat_id: str) -> bool:
        key = self._get_session_key(channel, chat_id)
        changed = False

        old_session = self._session_map.pop(key, None)
        if old_session:
            self._loaded_sessions.discard(old_session)
            changed = True

        if key in self._rehydrate_history:
            self._rehydrate_history.pop(key, None)
            changed = True

        if changed:
            self._save_session_map()
            logger.info(f"Cleared Stdio session state for {key}")

        return changed

    def list_sessions(self) -> dict[str, str]:
        return self._session_map.copy()
