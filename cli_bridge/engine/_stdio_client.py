"""Stdio-based ACP Connector for iflow CLI.

实现 ACP 协议连接器，直接通过 stdio 与 iflow 通信。
不需要启动 WebSocket 服务器，使用 iflow --experimental-acp 模式。

核心方法：
- initialize: 初始化连接，协商协议版本和能力
- session/create: 创建新会话
- session/prompt: 发送消息
- session/update: 接收更新通知
- session/cancel: 取消当前请求

消息类型：
- agent_message_chunk: Agent 响应块
- agent_thought_chunk: Agent 思考过程
- tool_call/tool_call_update: 工具调用
- stop_reason: 任务完成
"""

from __future__ import annotations

import asyncio
import json
import platform
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger

from cli_bridge.config.loader import DEFAULT_TIMEOUT


def _is_windows() -> bool:
    """检查是否为 Windows 平台。"""
    return platform.system().lower() == "windows"


class StdioACPError(Exception):
    """Stdio ACP 连接器错误基类。"""
    pass


class StdioACPConnectionError(StdioACPError):
    """Stdio ACP 连接错误。"""
    pass


class StdioACPTimeoutError(StdioACPError):
    """Stdio ACP 超时错误。"""
    pass


class StopReason(str, Enum):
    """任务结束原因。"""
    END_TURN = "end_turn"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class AgentMessageChunk:
    """Agent 消息块。"""
    text: str = ""
    is_thought: bool = False


@dataclass
class ToolCall:
    """工具调用信息。"""
    tool_call_id: str
    tool_name: str
    status: str = "pending"
    args: dict = field(default_factory=dict)
    output: str = ""


@dataclass
class ACPResponse:
    """ACP 响应结果。"""
    content: str = ""
    thought: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: StopReason | None = None
    error: str | None = None


class StdioACPClient:
    """
    Stdio ACP 协议客户端 - 与 iflow CLI 通过 stdio 通信。

    使用 subprocess 启动 iflow --experimental-acp，
    通过 stdin/stdout 进行 JSON-RPC 通信。

    Example:
        client = StdioACPClient(iflow_path="iflow", workspace="/path/to/workspace")
        await client.start()
        await client.initialize()
        session_id = await client.create_session(workspace="/path/to/workspace")
        response = await client.prompt(session_id, "Hello!")
        print(response.content)
    """

    PROTOCOL_VERSION = 1
    LINE_LIMIT = 10 * 1024 * 1024  # 10MB - readline 最大行长度，防止大 JSON 被截断

    def __init__(
        self,
        iflow_path: str = "iflow",
        workspace: Path | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        mcp_proxy_port: int = 8888,
        mcp_servers_auto_discover: bool = True,
        mcp_servers_max: int = 10,
        mcp_servers_allowlist: list[str] | None = None,
        mcp_servers_blocklist: list[str] | None = None,
        mcp_servers_cached: list[dict] | None = None,
    ):
        self.iflow_path = iflow_path
        self.workspace = workspace or Path.cwd()
        self.timeout = timeout
        self.mcp_proxy_port = mcp_proxy_port
        self.mcp_servers_auto_discover = mcp_servers_auto_discover
        self.mcp_servers_max = mcp_servers_max
        self.mcp_servers_allowlist = mcp_servers_allowlist or []
        self.mcp_servers_blocklist = mcp_servers_blocklist or []
        self.mcp_servers_cached = mcp_servers_cached

        self._process: asyncio.subprocess.Process | None = None
        self._started = False
        self._initialized = False
        self._request_id = 0
        self._pending_requests: dict[int, asyncio.Future] = {}
        self._receive_task: asyncio.Task | None = None
        self._stderr_receive_task: asyncio.Task | None = None  # 持续读取 stderr 防止缓冲区满导致死锁
        self._message_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._session_queues: dict[str, asyncio.Queue[dict]] = {}
        self._prompt_lock = asyncio.Lock()  # 保证请求写入的原子性，以及作为并发回退保障

        self._agent_capabilities: dict = {}

        logger.info(f"StdioACPClient initialized: {iflow_path}, workspace={workspace}")

    async def start(self) -> None:
        """启动 iflow 进程。"""
        if self._started:
            return

        try:
            if _is_windows():
                # Windows 上使用 shell 启动 iflow 命令，确保 .CMD 文件能被正确执行
                cmd = f'"{self.iflow_path}" --experimental-acp --stream'
                self._process = await asyncio.create_subprocess_shell(
                    cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.workspace),
                )
            else:
                # Unix 系统使用 exec 方式
                self._process = await asyncio.create_subprocess_exec(
                    self.iflow_path,
                    "--experimental-acp",
                    "--stream",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.workspace),
                )

            self._started = True

            # 设置 StreamReader 的 limit，防止大 JSON 行被截断
            if self._process.stdout:
                self._process.stdout._limit = self.LINE_LIMIT
            if self._process.stderr:
                self._process.stderr._limit = self.LINE_LIMIT

            self._receive_task = asyncio.create_task(self._receive_loop())
            self._stderr_receive_task = asyncio.create_task(self._stderr_receive_loop())

            logger.info(f"StdioACP started: pid={self._process.pid}")

            await asyncio.sleep(2)

        except Exception as e:
            raise StdioACPConnectionError(f"Failed to start iflow process: {e}") from e

    async def stop(self) -> None:
        """停止 iflow 进程。"""
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        if self._stderr_receive_task:
            self._stderr_receive_task.cancel()
            try:
                await self._stderr_receive_task
            except asyncio.CancelledError:
                pass
            self._stderr_receive_task = None

        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

        self._started = False
        self._initialized = False
        logger.info("StdioACP stopped")

    async def _receive_loop(self) -> None:
        """消息接收循环。"""
        while self._started and self._process and self._process.stdout:
            try:
                line = await asyncio.wait_for(
                    self._process.stdout.readline(),
                    timeout=1.0
                )

                if not line:
                    break

                raw = line.decode("utf-8", errors="replace").strip()

                if not raw:
                    continue

                if not raw.startswith("{"):
                    logger.debug(f"StdioACP non-JSON: {raw[:100]}")
                    continue

                try:
                    message = json.loads(raw)
                except json.JSONDecodeError as e:
                    logger.debug(f"StdioACP JSON decode error: {e}, raw={raw[:100]}")
                    continue

                if "id" in message:
                    request_id = message["id"]
                    if request_id in self._pending_requests:
                        future = self._pending_requests.pop(request_id)
                        if not future.done():
                            future.set_result(message)
                else:
                    # 这是一个通知，根据 sessionId 分发
                    params = message.get("params", {})
                    session_id = params.get("sessionId")
                    if session_id and session_id in self._session_queues:
                        await self._session_queues[session_id].put(message)
                    else:
                        # 如果没有 sessionId 或没有当前监听的 Session，放入全局队列
                        await self._message_queue.put(message)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except asyncio.LimitOverrunError as e:
                # 当单行超过 StreamReader limit 且未找到分隔符时，不要退出接收循环。
                # 丢弃当前异常大块并继续，避免网关在 Windows 上直接“启动后退出”。
                logger.warning(
                    f"StdioACP receive oversized chunk (consumed={e.consumed}), draining and continue"
                )
                try:
                    await self._process.stdout.readexactly(e.consumed)
                except Exception:
                    try:
                        await self._process.stdout.read(max(e.consumed, 4096))
                    except Exception:
                        pass
                continue
            except ValueError as e:
                # 某些 Python/平台组合会以 ValueError 形式抛出同类错误信息。
                msg = str(e)
                if "Separator is not found" in msg and "chunk exceed the limit" in msg:
                    logger.warning(f"StdioACP receive oversized chunk (value error): {msg}")
                    try:
                        await self._process.stdout.read(4096)
                    except Exception:
                        pass
                    continue
                logger.error(f"StdioACP receive error: {e}")
                break
            except Exception as e:
                logger.error(f"StdioACP receive error: {e}")
                break

        logger.debug("StdioACP receive loop ended")

    async def _stderr_receive_loop(self) -> None:
        """stderr 接收循环 - 持续读取 stderr 防止缓冲区满导致进程阻塞。"""
        while self._started and self._process and self._process.stderr:
            try:
                line = await asyncio.wait_for(
                    self._process.stderr.readline(),
                    timeout=1.0
                )

                if not line:
                    break

                raw = line.decode("utf-8", errors="replace").strip()

                if raw:
                    logger.debug(f"iflow stderr: {raw[:200]}")

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"StdioACP stderr receive error: {e}")
                break

        logger.debug("StdioACP stderr receive loop ended")

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send_request(
        self,
        method: str,
        params: dict,
        timeout: int | None = None,
    ) -> dict:
        """发送 JSON-RPC 请求并等待响应。"""
        if not self._started or not self._process:
            raise StdioACPConnectionError("ACP process not started")

        request_id = self._next_request_id()

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future

        try:
            request_str = json.dumps(request) + "\n"
            self._process.stdin.write(request_str.encode())
            await self._process.stdin.drain()
            logger.debug(f"StdioACP request: {method} (id={request_id})")

            timeout = timeout or self.timeout
            response = await asyncio.wait_for(future, timeout=timeout)

            if "error" in response:
                error = response["error"]
                raise StdioACPError(f"ACP error: {error.get('message', str(error))}")

            return response.get("result", {})

        except asyncio.TimeoutError as e:
            self._pending_requests.pop(request_id, None)
            raise StdioACPTimeoutError(f"ACP request timeout: {method}") from e

    async def initialize(self) -> dict:
        """初始化 ACP 连接。"""
        if self._initialized:
            return self._agent_capabilities

        client_capabilities = {
            "fs": {
                "readTextFile": True,
                "writeTextFile": True,
            }
        }

        result = await self._send_request("initialize", {
            "protocolVersion": self.PROTOCOL_VERSION,
            "clientCapabilities": client_capabilities,
        })

        self._agent_capabilities = result.get("agentCapabilities", {})
        self._initialized = True

        logger.info(f"StdioACP initialized: version={result.get('protocolVersion')}")

        return self._agent_capabilities

    async def _get_mcp_servers(self) -> list[dict]:
        """获取 MCP 服务器列表。

        优先级：
        1. 使用预缓存的服务器列表 (mcp_servers_cached)
        2. 自动从 MCP 代理发现 (mcp_servers_auto_discover=True)
        3. 降级为空列表（让 iflow 使用其默认配置）
        """
        # 优先使用预缓存的列表（由外部传入）
        if self.mcp_servers_cached:
            logger.debug(f"Using cached MCP servers: {len(self.mcp_servers_cached)}")
            return self.mcp_servers_cached

        # 自动发现
        if self.mcp_servers_auto_discover:
            try:
                from cli_bridge.utils.helpers import discover_mcp_servers
                servers = await discover_mcp_servers(
                    proxy_port=self.mcp_proxy_port,
                    allowlist=self.mcp_servers_allowlist or None,
                    blocklist=self.mcp_servers_blocklist or None,
                    max_servers=self.mcp_servers_max,
                )
                if servers:
                    return servers
            except ImportError as e:
                logger.warning(f"Failed to import discover_mcp_servers: {e}")
            except Exception as e:
                logger.warning(f"Failed to discover MCP servers: {e}")

        # 降级：返回空列表，让 iflow 使用其默认配置
        logger.debug("No MCP servers configured, using empty list")
        return []

    async def authenticate(self, method_id: str = "iflow") -> bool:
        """进行认证。"""
        if not self._initialized:
            await self.initialize()

        try:
            result = await self._send_request("authenticate", {
                "methodId": method_id,
            })
            success = result.get("methodId") == method_id
            if success:
                logger.info(f"StdioACP authenticated with method: {method_id}")
            return success
        except StdioACPError as e:
            logger.error(f"StdioACP authentication failed: {e}")
            return False

    async def create_session(
        self,
        workspace: Path | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
        approval_mode: str = "yolo",
    ) -> str:
        """创建新会话。"""
        if not self._initialized:
            await self.initialize()

        ws_path = str(workspace or self.workspace)

        # 获取 MCP 服务器列表（动态发现或缓存）
        mcp_servers = await self._get_mcp_servers()

        params: dict = {
            "cwd": ws_path,
            "mcpServers": mcp_servers,
        }

        settings: dict = {}
        if approval_mode:
            settings["permission_mode"] = approval_mode
        if model:
            settings["model"] = model
        if system_prompt:
            settings["system_prompt"] = system_prompt

        if settings:
            params["settings"] = settings

        result = await self._send_request("session/new", params)
        session_id = result.get("sessionId", "")

        if model:
            try:
                await self._send_request("session/set_model", {
                    "sessionId": session_id,
                    "modelId": model,
                }, timeout=10)
                logger.debug(f"Set model to {model} for session")
            except Exception as e:
                logger.warning(f"Failed to set model via session/set_model: {e}, trying set_config_option")
                try:
                    await self._send_request("session/set_config_option", {
                        "sessionId": session_id,
                        "configId": "model",
                        "value": model,
                    }, timeout=10)
                    logger.debug(f"Set model to {model} via set_config_option")
                except Exception as e2:
                    logger.debug(f"Failed to set model via set_config_option: {e2}")

        logger.info(f"StdioACP session created: {session_id[:16] if session_id else 'unknown'}...")

        return session_id

    async def load_session(self, session_id: str) -> bool:
        """加载已有会话。"""
        if not self._initialized:
            await self.initialize()

        try:
            result = await self._send_request("session/load", {
                "sessionId": session_id,
                "cwd": str(self.workspace),
                "mcpServers": [],
            })
            return result.get("loaded", False)
        except StdioACPError as e:
            logger.warning(f"Failed to load session {session_id[:16]}...: {e}")
            return False

    async def prompt(
        self,
        session_id: str,
        message: str,
        timeout: int | None = None,
        on_chunk: Callable[[AgentMessageChunk], Coroutine] | None = None,
        on_tool_call: Callable[[ToolCall], Coroutine] | None = None,
        on_event: Callable[[dict[str, Any]], Coroutine] | None = None,
    ) -> ACPResponse:
        """发送消息并获取响应。"""
        if not self._started or not self._process:
            raise StdioACPConnectionError("ACP process not started")

        response = ACPResponse()
        content_parts: list[str] = []
        thought_parts: list[str] = []
        tool_calls_map: dict[str, ToolCall] = {}

        request_id = self._next_request_id()

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": [
                    {"type": "text", "text": message}
                ],
            },
        }

        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future

        # 为当前 session 注册专用消息队列
        session_queue = asyncio.Queue()
        self._session_queues[session_id] = session_queue

        async with self._prompt_lock:
            try:
                request_str = json.dumps(request) + "\n"
                self._process.stdin.write(request_str.encode())
                await self._process.stdin.drain()
                logger.debug(f"StdioACP prompt sent (session={session_id[:16]}...)")
            except Exception as e:
                self._pending_requests.pop(request_id, None)
                self._session_queues.pop(session_id, None)
                raise e

        try:
            timeout = timeout or self.timeout
            # 使用空闲超时：每次收到消息后重置计时器
            # 这样长时间生成内容不会触发超时，只有真正卡住才会超时
            last_activity_time = asyncio.get_running_loop().time()

            while True:
                idle_time = asyncio.get_running_loop().time() - last_activity_time
                if idle_time >= timeout:
                    raise StdioACPTimeoutError("Prompt timeout (idle)")

                try:
                    if future.done():
                        break

                    # 优先检查自己的私有队列，找不到再看全局队列（Legacy 兼容）
                    try:
                        msg = await asyncio.wait_for(
                            session_queue.get(),
                            timeout=0.1
                        )
                        last_activity_time = asyncio.get_running_loop().time()  # 收到消息，重置计时器
                    except asyncio.TimeoutError:
                        # 尝试从全局队列获取（如果没有 sessionId 字段）
                        remaining_idle = timeout - idle_time
                        msg = await asyncio.wait_for(
                            self._message_queue.get(),
                            timeout=min(remaining_idle, 4.9)
                        )
                        last_activity_time = asyncio.get_running_loop().time()  # 收到消息，重置计时器

                    if msg.get("method") == "session/update":
                        params = msg.get("params", {})
                        update = params.get("update", {})
                        update_type = update.get("sessionUpdate", "")
                        if on_event:
                            await on_event(
                                {
                                    "session_id": session_id,
                                    "update_type": update_type,
                                    "update": update,
                                }
                            )

                        if update_type == "agent_message_chunk":
                            content = update.get("content", {})
                            if isinstance(content, dict) and content.get("type") == "text":
                                chunk_text = content.get("text", "")
                                if chunk_text:
                                    content_parts.append(chunk_text)
                                    if on_chunk:
                                        await on_chunk(AgentMessageChunk(text=chunk_text))

                        elif update_type == "agent_thought_chunk":
                            content = update.get("content", {})
                            if isinstance(content, dict) and content.get("type") == "text":
                                chunk_text = content.get("text", "")
                                if chunk_text:
                                    thought_parts.append(chunk_text)
                                    if on_chunk:
                                        await on_chunk(AgentMessageChunk(text=chunk_text, is_thought=True))

                        elif update_type == "tool_call":
                            tool_call_id = update.get("toolCallId", "")
                            tool_name = update.get("name", "")
                            args = update.get("args", {})

                            tc = ToolCall(
                                tool_call_id=tool_call_id,
                                tool_name=tool_name,
                                status="pending",
                                args=args,
                            )
                            tool_calls_map[tool_call_id] = tc

                            if on_tool_call:
                                await on_tool_call(tc)

                        elif update_type == "tool_call_update":
                            tool_call_id = update.get("toolCallId", "")
                            status = update.get("status", "")
                            output_text = ""

                            content = update.get("content", [])
                            if isinstance(content, list):
                                for c in content:
                                    if c.get("type") == "text":
                                        output_text += c.get("text", "")
                            elif isinstance(content, dict) and content.get("type") == "text":
                                output_text = content.get("text", "")

                            if tool_call_id in tool_calls_map:
                                tc = tool_calls_map[tool_call_id]
                                if status:
                                    tc.status = status
                                if output_text:
                                    tc.output = output_text

                                if on_tool_call:
                                    await on_tool_call(tc)

                except asyncio.TimeoutError:
                    continue

                if future.done():
                    break

            # 关键修复：future.done() 后，_receive_loop 可能还来得及把最后几个
            # agent_message_chunk 放入 _message_queue。这里 drain 掉所有残留消息，
            # 避免最后几段内容被丢弃导致消息截断。
            while True:
                try:
                    # 同样先 drain 自己的私有队列
                    try:
                        msg = await asyncio.wait_for(session_queue.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        # 尝试全局
                        msg = await asyncio.wait_for(self._message_queue.get(), timeout=0.1)

                    if msg.get("method") == "session/update":
                        params = msg.get("params", {})
                        update = params.get("update", {})
                        update_type = update.get("sessionUpdate", "")
                        if on_event:
                            await on_event(
                                {
                                    "session_id": session_id,
                                    "update_type": update_type,
                                    "update": update,
                                }
                            )
                        if update_type == "agent_message_chunk":
                            content = update.get("content", {})
                            if isinstance(content, dict) and content.get("type") == "text":
                                chunk_text = content.get("text", "")
                                if chunk_text:
                                    content_parts.append(chunk_text)
                                    if on_chunk:
                                        await on_chunk(AgentMessageChunk(text=chunk_text))
                except asyncio.TimeoutError:
                    break  # 队列已回，退出 drain

            final_response = future.result()

            if "error" in final_response:
                response.error = final_response["error"].get("message", str(final_response["error"]))
                response.stop_reason = StopReason.ERROR
            else:
                result = final_response.get("result", {})
                stop_reason_str = result.get("stopReason", "end_turn")
                try:
                    response.stop_reason = StopReason(stop_reason_str)
                except ValueError:
                    response.stop_reason = StopReason.END_TURN

            response.content = "".join(content_parts)
            response.thought = "".join(thought_parts)
            response.tool_calls = list(tool_calls_map.values())

            logger.debug(f"StdioACP prompt completed: stop_reason={response.stop_reason}")

            return response

        except asyncio.TimeoutError as e:
            self._pending_requests.pop(request_id, None)
            raise StdioACPTimeoutError("Prompt timeout") from e
        except StdioACPTimeoutError:
            self._pending_requests.pop(request_id, None)
            raise
        except Exception as e:
            self._pending_requests.pop(request_id, None)
            raise StdioACPError(f"Prompt error: {e}") from e
        finally:
            # 清理
            self._session_queues.pop(session_id, None)
            self._pending_requests.pop(request_id, None)

    async def cancel(self, session_id: str) -> None:
        """取消当前请求。"""
        if not self._started or not self._process:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": "session/cancel",
            "params": {
                "sessionId": session_id,
            },
        }

        try:
            notification_str = json.dumps(notification) + "\n"
            self._process.stdin.write(notification_str.encode())
            await self._process.stdin.drain()
            logger.debug(f"StdioACP cancel sent (session={session_id[:16]}...)")
        except Exception as e:
            logger.warning(f"Failed to send cancel: {e}")

    async def is_connected(self) -> bool:
        """检查连接状态。"""
        return self._started and self._process is not None and self._process.returncode is None

