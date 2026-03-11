# Todo: 解耦 iflow 延后事项

**关联特性**: `001-decouple-iflow-backend`
**来源**: speckit.clarify 澄清阶段未纳入本次范围的事项
**创建日期**: 2026-03-11
**状态**: 待处理

---

## 1. MCP 代理泛化（支持 Claude Code）

**背景**: 当前 MCP 代理（`cli_bridge/mcp_proxy.py`，配置字段 `mcp_proxy_*`）是 iflow 专属的，本次重构仅将其移入 iflow 配置块并隔离，未做泛化。

**需要做的事**:
- 设计通用的 MCP 代理接口，使 Claude Code 也能配置和使用 MCP 服务器
- 将 `mcp_proxy_*` 配置从 iflow 专属块提升为通用后端能力
- 更新文档，说明各后端的 MCP 支持状态

**优先级**: 中
**相关文件**: `cli_bridge/mcp_proxy.py`, `cli_bridge/config/schema.py`

---

## 2. Web UI 会话浏览支持 Claude Code

**背景**: Web UI 目前的会话浏览功能硬编码了 iflow 的会话目录（`~/.iflow/acp/sessions`）。本次重构仅做了重命名，未让 Web UI 真正支持浏览 Claude Code 的会话。

**需要做的事**:
- Web UI 会话列表页面需要能读取 Claude Code 的会话存储位置
- 根据当前激活的后端模式，展示对应后端的会话记录
- 考虑统一会话存储路径的抽象

**优先级**: 低
**相关文件**: `cli_bridge/web/server.py`（约 L665、L1283-L1291）

---

## 3. iflow passthrough 命令最终移除

**背景**: `cli_bridge/cli/iflow_passthrough.py` 在本次重构中保留并加了废弃警告，但计划在未来版本中彻底移除。

**需要做的事**:
- 确定移除的目标版本号
- 在 CHANGELOG / 发版说明中提前告知用户
- 移除 `iflow_passthrough.py` 及相关注册代码

**优先级**: 低
**相关文件**: `cli_bridge/cli/iflow_passthrough.py`, `cli_bridge/cli/commands.py`

---

## 4. 非功能属性：性能与可观测性基线

**背景**: 澄清阶段未覆盖性能目标和可观测性（日志、指标、链路追踪）。解耦完成后应补充这部分基线。

**需要做的事**:
- 确定 Claude Code 模式下的响应延迟基线目标
- 统一日志格式，确保 iflow 和 Claude Code 模式下日志结构一致
- 考虑是否需要添加后端切换事件的审计日志

**优先级**: 中
**相关文件**: `cli_bridge/engine/loop.py`, `cli_bridge/engine/claude_adapter.py`
