# cli-bridge

[![PyPI version](https://img.shields.io/pypi/v/cli-bridge.svg)](https://pypi.org/project/cli-bridge/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

一个自托管的多渠道 AI 网关，将聊天平台与 Claude 或 iflow 连接起来。

---

## 功能特性

- **9 个聊天平台集成** — Telegram、Discord、Slack、飞书、钉钉、QQ、WhatsApp、邮件、Mochat
- **2 种 AI 后端** — Claude CLI（通过 `claude-agent-sdk`）和 iflow CLI
- **流式响应** — 支持实时缓冲流式输出，适配渠道的原地编辑更新
- **MCP 服务器代理** — 聚合多个 MCP 服务器，跨会话共享
- **定时任务调度器** — 支持定期发送 AI 提示或执行任务
- **Web 控制台** — 内置 Web UI，用于监控和管理（端口 8787）
- **会话持久化** — 每用户会话映射在网关重启后仍可保留
- **工作区上下文** — 通过 `AGENTS.md` 向每条消息注入持久化指令

---

## 前置条件

- Python 3.10 或更高版本
- [uv](https://github.com/astral-sh/uv) 包管理器
- **Claude 后端：** 已安装并完成身份验证的 [Claude CLI](https://github.com/anthropics/claude-code)
- **iflow 后端：** 已安装的 iflow CLI
- 所需聊天平台的机器人令牌 / API 凭证

---

## 安装

```bash
git clone https://github.com/oyjs1989/cli_bridge.git
cd cli_bridge
uv sync
```

---

## 快速开始

```bash
# 1. 初始化配置文件和工作区
uv run cli-bridge onboard

# 2. 编辑配置文件，填入你的令牌
#    （参见下方配置说明）
nano ~/.cli-bridge/config.json

# 3. 在前台运行（开发 / 调试）
uv run cli-bridge gateway run

# 4. 或在后台运行（生产环境）
uv run cli-bridge gateway start

# 5. 查看运行状态
uv run cli-bridge status
```

---

## 配置说明

配置文件位于 `~/.cli-bridge/config.json`。运行 `cli-bridge onboard` 生成默认配置后，
根据需要进行编辑。

### 驱动（AI 后端）

**Claude 后端（推荐）：**

```json
{
  "driver": {
    "backend": "claude",
    "transport": "cli",
    "claude": {
      "model": "claude-opus-4-6",
      "permission_mode": "bypassPermissions"
    }
  }
}
```

**iflow 后端：**

```json
{
  "driver": {
    "backend": "iflow",
    "transport": "stdio"
  }
}
```

iflow 的 `transport` 选项：`cli`（每条消息启动子进程）、`stdio`（长驻进程）、`acp`（WebSocket 服务器）。

### 渠道配置示例

在配置文件的顶级 `"channels"` 对象中添加渠道配置以启用相应平台。

**Telegram：**

```json
"telegram": {
  "enabled": true,
  "token": "YOUR_BOT_TOKEN"
}
```

**Discord：**

```json
"discord": {
  "enabled": true,
  "token": "YOUR_BOT_TOKEN",
  "allow_from": ["YOUR_USER_ID"]
}
```

**Slack：**

```json
"slack": {
  "enabled": true,
  "bot_token": "xoxb-...",
  "app_token": "xapp-..."
}
```

**飞书（Lark）：**

```json
"feishu": {
  "enabled": true,
  "app_id": "cli_xxx",
  "app_secret": "YOUR_APP_SECRET",
  "encrypt_key": "YOUR_ENCRYPT_KEY",
  "verification_token": "YOUR_VERIFICATION_TOKEN"
}
```

---

## CLI 命令

| 命令 | 说明 |
|------|------|
| `gateway start` | 在后台启动网关 |
| `gateway run` | 在前台启动网关（调试模式） |
| `gateway stop` | 停止后台网关 |
| `gateway restart` | 重启后台网关 |
| `status` | 显示网关和渠道状态 |
| `model <name>` | 切换 AI 模型 |
| `thinking on\|off` | 开启/关闭扩展思考模式 |
| `sessions` | 管理会话映射 |
| `cron list\|add\|remove` | 管理定时任务 |
| `console` | 启动 Web UI（端口 8787） |
| `mcp-sync` | 从 iflow 同步 MCP 服务器 |
| `onboard` | 初始化配置和工作区 |

运行 `cli-bridge --help` 或 `cli-bridge <命令> --help` 查看完整选项说明。

---

## 工作区 / 上下文文件

在 `~/.cli-bridge/workspace/` 中放置 Markdown 文件，可向 AI 注入上下文：

| 文件 | 行为 |
|------|------|
| `AGENTS.md` | 作为持久化系统上下文注入**每条**消息 |
| `BOOTSTRAP.md` | 仅在**首次运行**时注入一次，之后自动清除 |
| `HEARTBEAT.md` | 定时心跳提示的模板（需配置 cron） |

`AGENTS.md` 示例：

```markdown
You are a helpful assistant. Always reply in the same language the user writes in.
Keep responses concise unless asked for detail.
```

---

## MCP 服务器支持

cli-bridge 内置 MCP 代理，可聚合多个 MCP 服务器并在单个会话中将其统一暴露给 AI 后端。

在 `~/.cli-bridge/config/.mcp_proxy_config.json` 中配置 MCP 服务器，然后运行：

```bash
uv run cli-bridge mcp-sync
```

该命令会将 iflow 安装中的 MCP 服务器列表同步到代理配置中。

---

## 常见问题

**网关无法启动**

运行 `cli-bridge status` 查看错误详情。检查 `~/.cli-bridge/` 中的日志文件获取堆栈信息。
确认所需的 CLI 工具（`claude` 或 `iflow`）已添加到系统 `PATH`。

**机器人不响应消息**

确认机器人令牌正确，且机器人在对应平台处于活跃状态。对于 Discord 等渠道，请检查
`allow_from` 列表——来自未列出用户 ID 的消息会被静默忽略。

**重启后会话丢失**

会话映射存储在 `~/.cli-bridge/session_mappings.json` 中。如需强制重置：

```bash
uv run cli-bridge sessions --clear
```

**iflow 后端响应缓慢**

切换到 `stdio` 传输模式，使 iflow 在消息间保持运行，而不是每条消息都启动新进程：

```json
"driver": { "backend": "iflow", "transport": "stdio" }
```

**Web 控制台无法加载**

确认端口 8787 未被占用，然后单独启动控制台：

```bash
uv run cli-bridge console
```

---

## 许可证

MIT — 详见 [LICENSE](LICENSE) 文件。
