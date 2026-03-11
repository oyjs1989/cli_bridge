"""配置 schema for cli-bridge。"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings


# 导入统一的超时常量，避免循环导入问题
def _get_default_timeout() -> int:
    """延迟导入避免循环依赖。"""
    from cli_bridge.config.loader import DEFAULT_TIMEOUT

    return DEFAULT_TIMEOUT


# ============================================================================
# 渠道配置
# ============================================================================


class TelegramConfig(BaseModel):
    model_config = {"extra": "ignore"}

    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)


class DiscordConfig(BaseModel):
    model_config = {"extra": "ignore"}

    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)


class WhatsAppConfig(BaseModel):
    model_config = {"extra": "ignore"}

    enabled: bool = False
    bridge_url: str = "http://localhost:3001"
    bridge_token: str = ""
    allow_from: list[str] = Field(default_factory=list)


class FeishuConfig(BaseModel):
    model_config = {"extra": "ignore"}

    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    verification_token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    ssl_verify: bool = True
    """是否验证 SSL 证书。内网自签名证书环境可设为 false。"""


class SlackConfig(BaseModel):
    model_config = {"extra": "ignore"}

    class DMConfig(BaseModel):
        model_config = {"extra": "ignore"}

        enabled: bool = True
        policy: Literal["open", "allowlist"] = "open"
        allow_from: list[str] = Field(default_factory=list)

    enabled: bool = False
    bot_token: str = ""
    app_token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    group_policy: Literal["mention", "open", "allowlist"] = "mention"
    group_allow_from: list[str] = Field(default_factory=list)
    reply_in_thread: bool = True
    react_emoji: str = "eyes"
    dm: DMConfig = Field(default_factory=DMConfig)


class DingTalkConfig(BaseModel):
    model_config = {"extra": "ignore"}

    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""
    robot_code: str = ""  # 机器人代码（群聊需要）
    card_template_id: str = ""  # AI Card 模板 ID（流式输出需要）
    card_template_key: str = "content"  # AI Card 内容字段名
    allow_from: list[str] = Field(default_factory=list)


class QQConfig(BaseModel):
    model_config = {"extra": "ignore"}

    enabled: bool = False
    app_id: str = ""
    secret: str = ""
    allow_from: list[str] = Field(default_factory=list)
    split_threshold: int = 3
    """流式分段发送阈值（基于换行符数量）。

    - 0: 不分段，等 AI 全部输出完后一次性发送
    - N > 0: 流式接收时每累积 N 个换行符立即推送一条新 QQ 消息，剩余内容在结束时补发
    """
    groups: list[str] = Field(default_factory=list)
    """允许加入的 QQ 群号列表（guild_id）"""
    markdown_support: bool = False
    """是否启用 Markdown 消息格式（默认 False）。

    启用前需在 QQ 开放平台申请 Markdown 消息权限。
    启用后消息格式为 { markdown: { content }, msg_type: 2 }。
    禁用时使用纯文本格式 { content, msg_type: 0 }。
    """


class EmailConfig(BaseModel):
    model_config = {"extra": "ignore"}

    enabled: bool = False
    consent_granted: bool = False
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_use_ssl: bool = True
    """IMAP 是否使用 SSL 连接。"""
    imap_mailbox: str = "INBOX"
    """IMAP 邮箱文件夹。"""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    """SMTP 是否使用 STARTTLS。"""
    from_address: str = ""
    allow_from: list[str] = Field(default_factory=list)
    auto_reply_enabled: bool = True
    poll_interval_seconds: int = 30
    """IMAP 轮询间隔（秒）。"""
    max_body_chars: int = 10000
    """邮件正文最大字符数。"""
    mark_seen: bool = True
    """获取后是否标记为已读。"""
    subject_prefix: str = "Re: "
    """回复邮件主题前缀。"""


class MochatConfig(BaseModel):
    model_config = {"extra": "ignore"}

    enabled: bool = False
    base_url: str = "https://mochat.io"
    socket_url: str = "https://mochat.io"
    socket_path: str = "/socket.io"
    claw_token: str = ""
    agent_user_id: str = ""
    sessions: list[str] = Field(default_factory=lambda: ["*"])
    panels: list[str] = Field(default_factory=lambda: ["*"])
    reply_delay_mode: str = ""
    """延迟回复模式。设为 'non-mention' 时，非 @ 消息会延迟发送。"""
    reply_delay_ms: int = 120000
    """延迟回复时间（毫秒），默认 120 秒。"""


class ChannelsConfig(BaseModel):
    model_config = {"extra": "ignore"}

    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    dingtalk: DingTalkConfig = Field(default_factory=DingTalkConfig)
    qq: QQConfig = Field(default_factory=QQConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    mochat: MochatConfig = Field(default_factory=MochatConfig)

    send_progress: bool = True
    send_tool_hints: bool = True


# ============================================================================
# Driver 配置（后端设置）
# ============================================================================


class IFlowBackendConfig(BaseModel):
    """iflow 后端专属配置。

    仅在 driver.mode ∈ {cli, stdio, acp} 时使用。
    """

    model_config = {"extra": "ignore"}

    iflow_path: str = "iflow"
    """iflow 二进制路径。"""

    model: str = "minimax-m2.5"
    """默认模型名称。"""

    yolo: bool = True
    """自动审批模式（跳过所有确认提示）。"""

    thinking: bool = False
    """启用扩展思考模式。"""

    extra_args: list[str] = Field(default_factory=list)
    """传递给 iflow CLI 的额外参数。"""

    compression_trigger_tokens: int = 60000
    """活跃会话自动压缩触发阈值（估算 token）。"""

    # ACP 模式配置
    acp_host: str = "localhost"
    """ACP 模式下的主机地址。"""

    acp_port: int = 8090
    """ACP 模式下的端口号。"""

    # MCP 代理配置
    disable_mcp: bool = False
    """是否禁用 MCP 服务器（减少资源消耗）。"""

    mcp_proxy_enabled: bool = True
    """是否启用 MCP 代理（共享 MCP 服务器以减少资源消耗）。"""

    mcp_proxy_port: int = 8888
    """MCP 代理服务器的端口号。"""

    mcp_proxy_auto_start: bool = True
    """是否在启动网关时自动启动 MCP 代理。"""

    mcp_servers_auto_discover: bool = True
    """是否自动从 MCP 代理发现启用的服务器。"""

    mcp_servers_max: int = 10
    """单个 iflow 实例最多连接的 MCP 服务器数量。"""

    mcp_servers_allowlist: list[str] = Field(default_factory=list)
    """允许使用的 MCP 服务器名称列表（空表示使用所有）。"""

    mcp_servers_blocklist: list[str] = Field(default_factory=list)
    """禁用的 MCP 服务器名称列表（优先级高于 allowlist）。"""


class ClaudeBackendConfig(BaseModel):
    """Claude Code 后端专属配置。

    仅在 driver.mode == "claude" 时使用。
    """

    model_config = {"extra": "ignore"}

    claude_path: str = "claude"
    """claude CLI 二进制路径。"""

    model: str = "claude-opus-4-6"
    """通过 --model 传递的模型 ID。"""

    system_prompt: str = ""
    """追加到 Claude 默认系统提示词的静态内容。空字符串表示使用 Claude 默认值。"""

    permission_mode: Literal["default", "acceptEdits", "bypassPermissions"] = (
        "bypassPermissions"
    )
    """Claude 工具执行权限模式。bypassPermissions = 跳过所有确认提示。"""


class DriverConfig(BaseModel):
    """后端驱动配置（共享字段 + 后端专属嵌套对象）。"""

    model_config = {"extra": "ignore"}

    mode: Literal["cli", "acp", "stdio", "claude"] = "stdio"
    """通信模式: cli (子进程调用), acp (WebSocket), stdio (直接通过 stdin/stdout), 或 claude (Claude Code)。"""

    max_turns: int = 40
    """最大对话轮数（所有后端共用）。"""

    timeout: int = Field(default_factory=_get_default_timeout)
    """请求超时时间（秒，所有后端共用）。"""

    workspace: str = ""
    """工作目录路径（所有后端共用）。空字符串使用默认路径 ~/.cli-bridge/workspace。"""

    # 后端专属嵌套配置
    iflow: IFlowBackendConfig | None = None
    """iflow 专属配置。mode ∈ {cli, stdio, acp} 时自动填充默认值。"""

    claude: ClaudeBackendConfig | None = None
    """Claude Code 专属配置。mode == "claude" 时自动填充默认值。"""

    @model_validator(mode="after")
    def populate_backend_config(self) -> "DriverConfig":
        """根据 mode 自动填充对应的后端配置（如未显式提供）。"""
        if self.mode in ("cli", "stdio", "acp") and self.iflow is None:
            self.iflow = IFlowBackendConfig()
        elif self.mode == "claude" and self.claude is None:
            self.claude = ClaudeBackendConfig()
        return self


# ============================================================================
# 主配置
# ============================================================================


class Config(BaseSettings):
    """cli-bridge 主配置。

    配置统一放在 driver 下，避免重复字段。
    """

    model_config = {
        "env_prefix": "CLI_BRIDGE_",
        "env_nested_delimiter": "__",
        "extra": "ignore",
    }

    # Driver 配置（包含 model, workspace, timeout 等）
    driver: DriverConfig = Field(default_factory=DriverConfig)

    # 渠道配置
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)

    # 日志
    log_level: str = "INFO"
    log_file: str = ""

    def get_enabled_channels(self) -> list[str]:
        """获取已启用的渠道列表。"""
        enabled = []
        for name in [
            "telegram",
            "discord",
            "whatsapp",
            "feishu",
            "slack",
            "dingtalk",
            "qq",
            "email",
            "mochat",
        ]:
            channel = getattr(self.channels, name, None)
            if channel and getattr(channel, "enabled", False):
                enabled.append(name)
        return enabled

    def get_workspace(self) -> str:
        """获取 workspace 路径。

        优先使用 driver.workspace，默认为 ~/.cli-bridge/workspace
        """
        if self.driver and self.driver.workspace:
            return self.driver.workspace
        return str(Path.home() / ".cli-bridge" / "workspace")

    def get_model(self) -> str:
        """获取模型名称。

        根据 driver.mode 从对应的后端配置中读取模型名称。
        """
        if self.driver.mode in ("cli", "stdio", "acp") and self.driver.iflow:
            return self.driver.iflow.model
        elif self.driver.mode == "claude" and self.driver.claude:
            return self.driver.claude.model
        return "minimax-m2.5"

    def get_timeout(self) -> int:
        """获取超时时间。"""
        if self.driver and self.driver.timeout:
            return self.driver.timeout
        return _get_default_timeout()
