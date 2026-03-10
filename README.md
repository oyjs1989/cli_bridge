# 🤖 cli-bridge

```
/$$ /$$$$$$$$ /$$                                 /$$$$$$$              /$$    
|__/| $$_____/| $$                                | $$__  $$            | $$    
 /$$| $$      | $$  /$$$$$$  /$$  /$$  /$$        | $$  \ $$  /$$$$$$  /$$$$$$  
| $$| $$$$$   | $$ /$$__  $$| $$ | $$ | $$ /$$$$$$| $$$$$$$  /$$__  $$|_  $$_/  
| $$| $$__/   | $$| $$  \ $$| $$ | $$ | $$|______/| $$__  $$| $$  \ $$  | $$    
| $$| $$      | $$| $$  | $$| $$ | $$ | $$        | $$  \ $$| $$  \ $$  | $$ /$$
| $$| $$      | $$|  $$$$$$/|  $$$$$/$$$$/        | $$$$$$$/|  $$$$$$/  |  $$$$/
|__/|__/      |__/ \______/  \_____/\___/         |_______/  \______/    \___/     
```

**English** | [中文](README_CN.md)

**Multi-Channel AI Assistant** - A multi-platform messaging bot built on iflow CLI.

Extend the powerful AI capabilities of iflow to multiple communication platforms, making AI assistants accessible everywhere.

## ✨ Features

- 🔌 **Multi-Channel Support** - Telegram, Discord, Slack, Feishu, DingTalk, QQ, WhatsApp, Email, Mochat
- 🧠 **AI-Powered** - Built on iflow CLI, supporting multiple models (GLM-5, Kimi K2.5, MiniMax M2.5, etc.)
- 💾 **Session Management** - Automatic multi-user session management with conversation context support
- 📁 **Workspace** - Each bot instance has its own independent workspace and memory system
- 🔐 **Access Control** - Supports whitelist, mention trigger, and various other policies
- 🔄 **Thinking Mode** - Optional AI thinking process display
- ⚡ **Streaming Output** - Real-time streaming output support for Telegram and DingTalk AI Card
- 🚀 **Stdio Mode** - Direct communication with iflow via stdin/stdout for faster response

## 🎬 Demo

### Telegram Streaming Output

![Telegram Streaming Output Demo](https://github.com/kai648846760/cli-bridge/raw/master/testcase/Lark20260225-200437.gif)

### DingTalk AI Card Streaming Output

![DingTalk AI Card Streaming Output Demo](https://github.com/kai648846760/cli-bridge/raw/master/testcase/Lark20260225-200423.gif)

## 📋 Prerequisites

### 1. Install iflow CLI

cli-bridge depends on iflow CLI. Please install it first:

```bash
# With Node.js 22+
npm i -g @iflow-ai/iflow-cli@latest
```

### 2. Login to iflow

```bash
iflow
```
1. After running iflow, select "Login with iFlow"
2. CLI will automatically open browser to iFlow platform
3. Complete registration/login and authorize iFlow CLI
4. Return to terminal automatically and start using

Follow the prompts to complete the login process.

## 🚀 Quick Start

### Installation

**Option 1: pip install (Recommended)**

```bash
pip install cli-bridge
```

After installation, you can use it directly:

```bash
cli-bridge --help
cli-bridge onboard
cli-bridge gateway start
```

**Option 2: Install from Source**

```bash
# Clone repository
git clone https://github.com/your-repo/cli-bridge.git
cd cli-bridge

# Install dependencies (using uv)
uv sync
```

### Initialize Configuration

```bash
# Create default config file (pip install)
cli-bridge onboard

# Or from source
uv run cli-bridge onboard

# Or manually
mkdir -p ~/.cli-bridge
cp config.example.json ~/.cli-bridge/config.json
```

### Start Service

**After pip install:**

```bash
# Foreground (debug mode)
cli-bridge gateway run

# Background
cli-bridge gateway start

# Check status
cli-bridge status

# Stop service
cli-bridge gateway stop
```

**After source install:**

```bash
# Foreground (debug mode)
uv run cli-bridge gateway run

# Background
uv run cli-bridge gateway start
```

## 🐳 Docker Deployment

```bash
# Build image
docker build -t cli-bridge:latest .

# Prepare host config file
mkdir -p ./config
cp config/config.example.json ./config/config.json
```

Then edit `./config/config.json` to enable channels and tokens.

```bash
# Start with docker compose
docker compose up -d

# View logs
docker compose logs -f cli-bridge
```

## ⚙️ Configuration

Configuration file located at `~/.cli-bridge/config.json`

### Full Configuration Example

```json
{
  "driver": {
    "mode": "stdio",
    "iflow_path": "iflow",
    "model": "minimax-m2.5",
    "yolo": true,
    "thinking": false,
    "max_turns": 40,
    "timeout": 180,
    "workspace": "~/.cli-bridge/workspace",
    "extra_args": []
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allow_from": []
    },
    "discord": {
      "enabled": false,
      "token": "YOUR_BOT_TOKEN",
      "allow_from": []
    },
    "slack": {
      "enabled": false,
      "bot_token": "xoxb-xxx",
      "app_token": "xapp-xxx",
      "allow_from": [],
      "group_policy": "mention"
    },
    "feishu": {
      "enabled": false,
      "app_id": "cli_xxx",
      "app_secret": "xxx",
      "encrypt_key": "",
      "verification_token": "",
      "allow_from": []
    },
    "dingtalk": {
      "enabled": false,
      "client_id": "xxx",
      "client_secret": "xxx",
      "robot_code": "xxx",
      "card_template_id": "xxx-xxx-xxx",
      "card_template_key": "content",
      "allow_from": []
    },
    "qq": {
      "enabled": false,
      "app_id": "xxx",
      "secret": "xxx",
      "allow_from": []
    },
    "whatsapp": {
      "enabled": false,
      "bridge_url": "http://localhost:3001",
      "bridge_token": "",
      "allow_from": []
    },
    "email": {
      "enabled": false,
      "consent_granted": false,
      "imap_host": "imap.gmail.com",
      "imap_port": 993,
      "imap_username": "your@email.com",
      "imap_password": "app_password",
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 587,
      "smtp_username": "your@email.com",
      "smtp_password": "app_password",
      "from_address": "your@email.com",
      "allow_from": [],
      "auto_reply_enabled": true
    },
    "mochat": {
      "enabled": false,
      "base_url": "https://mochat.io",
      "socket_url": "https://mochat.io",
      "socket_path": "/socket.io",
      "claw_token": "xxx",
      "agent_user_id": "",
      "sessions": ["*"],
      "panels": ["*"]
    }
  },
  "log_level": "INFO",
  "log_file": ""
}
```

### Driver Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mode` | string | `"stdio"` | Communication mode: `stdio` (recommended), `acp` (WebSocket), `cli` (subprocess) |
| `iflow_path` | string | `"iflow"` | iflow CLI path [keep default] |
| `model` | string | `"minimax-m2.5"` | Default model (glm-5, kimi-k2.5, minimax-m2.5, etc.) |
| `yolo` | bool | `true` | Auto-confirm mode |
| `thinking` | bool | `false` | Show AI thinking process |
| `max_turns` | int | `40` | Maximum conversation turns per session |
| `timeout` | int | `180` | Timeout in seconds |
| `workspace` | string | `~/.cli-bridge/workspace` | Workspace path |
| `extra_args` | list | `[]` | Additional iflow arguments |
| `acp_port` | int | `8090` | Port for ACP mode |
| `acp_host` | string | `"localhost"` | Host for ACP mode |

#### Communication Modes

**Stdio Mode (⭐ Recommended)**:
- Direct communication with iflow via stdin/stdout
- No need to start WebSocket service, faster startup
- Real-time streaming output, typewriter effect
- Lower response latency, close to native experience
- Gateway automatically executes `iflow --experimental-acp --stream` on startup

**ACP Mode (WebSocket)**:
- Communication with iflow via WebSocket
- Requires starting WebSocket server [auto-started]
- Real-time streaming output support
- Suitable for scenarios requiring remote connection

**CLI Mode**:
- Call iflow CLI via subprocess
- Each conversation starts independent process
- Suitable for simple scenarios or debugging

#### Recommended Configuration

```json
{
  "driver": {
    "mode": "stdio",
    "model": "minimax-m2.5",
    "thinking": false,
    "yolo": true
  }
}
```

### Channel Configuration

#### Telegram

```json
{
  "telegram": {
    "enabled": true,
    "token": "YOUR_BOT_TOKEN",
    "allow_from": ["user_id_1", "user_id_2"]
  }
}
```

1. Create bot at [@BotFather](https://t.me/BotFather) to get Token
2. Empty `allow_from` allows all users

#### Discord

```json
{
  "discord": {
    "enabled": true,
    "token": "YOUR_BOT_TOKEN",
    "allow_from": ["user_id_1"]
  }
}
```

1. Create application at [Discord Developer Portal](https://discord.com/developers/applications)
2. Create Bot user and get Token
3. Enable Message Content Intent

#### Slack

```json
{
  "slack": {
    "enabled": true,
    "bot_token": "xoxb-xxx",
    "app_token": "xapp-xxx",
    "allow_from": [],
    "group_policy": "mention"
  }
}
```

1. Create application at [Slack API](https://api.slack.com/apps)
2. Create Bot and get Bot Token (`xoxb-xxx`)
3. Enable Socket Mode to get App Token (`xapp-xxx`)
4. `group_policy` controls channel message response strategy:
   - `mention`: Only respond to @mentions
   - `open`: Respond to all messages
   - `allowlist`: Only respond to whitelisted channels

#### Feishu/Lark

```json
{
  "feishu": {
    "enabled": true,
    "app_id": "cli_xxx",
    "app_secret": "xxx",
    "encrypt_key": "",
    "verification_token": "",
    "allow_from": []
  }
}
```

1. Create enterprise self-built app at [Feishu Open Platform](https://open.feishu.cn/)
2. Enable bot capability
3. Configure event subscription (uses WebSocket, no public IP required)

#### DingTalk

```json
{
  "dingtalk": {
    "enabled": true,
    "client_id": "xxx",
    "client_secret": "xxx",
    "robot_code": "xxx",
    "card_template_id": "xxx-xxx-xxx",
    "card_template_key": "content",
    "allow_from": []
  }
}
```

1. Create bot at [DingTalk Open Platform](https://open.dingtalk.com/)
2. Get Client ID and Client Secret
3. Enable Stream Mode (no public IP required)

**AI Card Streaming Output Configuration** (optional, for typewriter effect):

| Parameter | Description |
|-----------|-------------|
| `robot_code` | Robot code, required for group chats |
| `card_template_id` | AI Card template ID, create in DingTalk developer console |
| `card_template_key` | Template content field name, default `content` |

**Create AI Card Template**:
1. Login to [DingTalk Developer Console](https://open.dingtalk.com/)
2. Go to "Card Platform" → "Card Templates"
3. Create template, add a "Text" type field
4. Record template ID and field name, configure in `card_template_id` and `card_template_key`

**Streaming Output Effect**:
- Bot immediately replies with a blank card after user sends message
- Card content updates in real-time, typewriter effect
- No need to wait for complete response, smoother experience

#### QQ

```json
{
  "qq": {
    "enabled": true,
    "app_id": "xxx",
    "secret": "xxx",
    "allow_from": []
  }
}
```

1. Create bot at [QQ Open Platform](https://q.qq.com/)
2. Get App ID and Secret

#### WhatsApp

```json
{
  "whatsapp": {
    "enabled": true,
    "bridge_url": "http://localhost:3001",
    "bridge_token": "",
    "allow_from": []
  }
}
```

Requires deploying [WhatsApp Bridge](https://github.com/your-repo/whatsapp-bridge) (based on baileys)

#### Email

```json
{
  "email": {
    "enabled": true,
    "consent_granted": true,
    "imap_host": "imap.gmail.com",
    "imap_port": 993,
    "imap_username": "your@email.com",
    "imap_password": "app_password",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_username": "your@email.com",
    "smtp_password": "app_password",
    "from_address": "your@email.com",
    "allow_from": ["sender@example.com"],
    "auto_reply_enabled": true
  }
}
```

**Important**: Using Gmail requires creating an App Password

#### Mochat

```json
{
  "mochat": {
    "enabled": true,
    "base_url": "https://mochat.io",
    "socket_url": "https://mochat.io",
    "socket_path": "/socket.io",
    "claw_token": "xxx",
    "agent_user_id": "",
    "sessions": ["*"],
    "panels": ["*"]
  }
}
```

## 🎮 CLI Commands

### Basic Commands

```bash
# Show version
cli-bridge version
cli-bridge -v

# Show help
cli-bridge --help

# Check status
cli-bridge status

# Initialize config
cli-bridge onboard [--force]

# Start web console
cli-bridge console --host 127.0.0.1 --port 8787
# Optional access token
cli-bridge console --token your_token
```

### Gateway Service Management

```bash
# Start service in background
cli-bridge gateway start

# Run in foreground (debug mode)
cli-bridge gateway run

# Stop service
cli-bridge gateway stop

# Restart service
cli-bridge gateway restart
```

### Configuration Management

```bash
# Show config
cli-bridge config --show

# Edit config
cli-bridge config -e

# Switch model
cli-bridge model glm-5
cli-bridge model kimi-k2.5
cli-bridge model minimax-m2.5

# Thinking mode
cli-bridge thinking on
cli-bridge thinking off
```

### Session Management

```bash
# List all sessions
cli-bridge sessions

# Filter by channel
cli-bridge sessions --channel telegram

# Filter by chat ID
cli-bridge sessions --chat-id 123456

# Clear session mappings
cli-bridge sessions --clear
```

### Scheduled Tasks (Cron)

```bash
# List tasks
cli-bridge cron list [-a]

# Add interval task
cli-bridge cron add -n "Water reminder" -m "Time to drink water!" -e 300 -d --channel telegram --to "123456"

# Add one-time task
cli-bridge cron add -n "Meeting reminder" -m "Meeting time!" -a "2024-12-25T10:00:00" -d --channel telegram --to "123456"

# Add cron expression task
cli-bridge cron add -n "Morning report" -m "Send morning report" -c "0 9 * * *" -d --channel telegram --to "123456"

# Enable/disable task
cli-bridge cron enable <id>
cli-bridge cron disable <id>

# Run task immediately
cli-bridge cron run <id>

# Remove task
cli-bridge cron remove <id>
```

### iflow Command Passthrough

```bash
# iflow basic passthrough
cli-bridge iflow --help
cli-bridge iflow -p "hello"

# MCP commands
cli-bridge mcp --help

# Agent commands
cli-bridge agent --help

# Workflow commands
cli-bridge workflow --help

# Skill commands
cli-bridge skill --help

# Commands
cli-bridge commands --help
```

## 📁 Directory Structure

```
~/.cli-bridge/
├── botpy.log                # QQ bot log
├── config.json              # Configuration file
├── gateway.pid              # PID file (background mode)
├── gateway.log              # Gateway log
├── session_mappings.json    # Session mappings
├── workspace/               # iflow workspace
│   ├── AGENTS.md            # Agent behavior guide
│   ├── BOOT.md              # Boot configuration
│   ├── HEARTBEAT.md         # Heartbeat tasks
│   ├── IDENTITY.md          # Identity
│   ├── SOUL.md              # AI personality definition
│   ├── TOOLS.md             # Tool configuration
│   ├── USER.md              # User info
│   └── memory/              # Memory directory
│       └── MEMORY.md        # Long-term memory
└── data/                    # Data directory
    └── cron/                # Scheduled tasks
        └── jobs.json        # Task data
```

## 🔧 Development

### Streaming Output Support

cli-bridge supports real-time streaming output, allowing users to see AI "typing".

**Channels with Streaming Support**:
| Channel | Method | Description |
|---------|--------|-------------|
| Telegram | Edit message | Real-time message content editing |
| DingTalk | AI Card | Streaming update using DingTalk card template |
| Discord | Edit message | Real-time message content editing (planned) |
| Slack | Edit message | Real-time message content editing (planned) |

**Configuration Requirements**:
- Use Stdio mode (`driver.mode = "stdio"`) or ACP mode (`driver.mode = "acp"`)
- DingTalk requires additional AI Card template configuration

**Streaming Output Buffer Mechanism**:
- Push update when content accumulates 10-25 characters (random)
- Avoid overly frequent API calls
- Ensure final message contains all content

### Session Management

cli-bridge automatically manages multi-user sessions with cross-channel conversation context support.

**Session Mapping Storage**:
- Location: `~/.cli-bridge/session_mappings.json`
- Format: `{channel}:{chat_id} -> {sessionId}`

**Session Recovery Mechanism**:
- Automatically restore sessions after Gateway restart
- Create new session when session expires
- Support session management via CLI

```bash
# View all sessions
cli-bridge sessions

# Clear session mappings
cli-bridge sessions --clear
```

### Project Structure

```
cli-bridge/
├── cli_bridge/
│   ├── __init__.py
│   ├── __main__.py          # Entry point
│   ├── bus/                 # Message bus
│   │   ├── events.py        # Event definitions
│   │   └── queue.py         # Message queue
│   ├── channels/            # Channel implementations
│   │   ├── base.py          # Base class
│   │   ├── telegram.py
│   │   ├── discord.py
│   │   ├── slack.py
│   │   ├── feishu.py
│   │   ├── dingtalk.py
│   │   ├── qq.py
│   │   ├── whatsapp.py
│   │   ├── email.py
│   │   ├── mochat.py
│   │   └── manager.py       # Channel manager
│   ├── cli/                 # CLI commands
│   │   └── commands.py
│   ├── config/              # Configuration management
│   │   ├── schema.py        # Configuration model
│   │   └── loader.py
│   ├── cron/                # Scheduled tasks
│   │   ├── service.py
│   │   └── types.py
│   ├── engine/              # Core engine
│   │   ├── adapter.py       # iflow adapter
│   │   ├── acp.py          # ACP mode (WebSocket)
│   │   ├── stdio_acp.py    # Stdio mode
│   │   └── loop.py          # Message loop
│   ├── heartbeat/           # Heartbeat service
│   │   └── service.py
│   ├── session/             # Session management
│   │   └── manager.py
│   ├── templates/           # Template files
│   │   ├── AGENTS.md
│   │   ├── SOUL.md
│   │   └── ...
│   └── utils/               # Utility functions
│       └── helpers.py
├── tests/
├── pyproject.toml
└── README.md
```

## 📝 Workspace Templates

Workspace contains AI's "personality" and memory:

- **SOUL.md** - Defines AI's core personality and behavior guidelines
- **USER.md** - User information and preferences
- **AGENTS.md** - Workspace behavior guide
- **TOOLS.md** - Available tools and configuration
- **MEMORY.md** - Long-term memory (important events, decisions)
- **memory/YYYY-MM-DD.md** - Daily memory logs

## 🤝 Contributing

Issues and Pull Requests are welcome!

## 📄 License

MIT

## 🙏 Acknowledgments

- [iflow CLI](https://cli.iflow.cn/) - Powerful AI Agent CLI
- [nanobot](https://github.com/HKUDS/nanobot) - Lightweight AI bot framework
