# Feature Specification: Decouple iflow Backend for Multi-Backend Support

**Feature Branch**: `001-decouple-iflow-backend`
**Created**: 2026-03-11
**Status**: Draft
**Input**: User description: "当前项目耦合iflow太多。先分析所有的代码，然后给出方案。项目目标是支持iflow和claude code。所以iflow特性相关的都要迁移走。"

## Overview

The gateway currently mixes iflow-specific details (commands, configuration, session paths, protocol implementations) throughout its core code, making it impossible to run the system with only Claude Code as the backend. The goal is to cleanly separate iflow-specific concerns into an isolated layer so the system can support iflow, Claude Code, or any future backend interchangeably.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Claude Code Exclusive Operation (Priority: P1)

An operator wants to run the gateway backed entirely by Claude Code, without installing iflow at all. Currently the system fails to start or warns about missing iflow even when Claude mode is selected.

**Why this priority**: This is the core goal — the system should work fully with Claude Code as a standalone backend without iflow present.

**Independent Test**: Configure the gateway with `driver.mode = "claude"`, ensure iflow is not installed, start the gateway, send a message from any channel, and verify a valid AI response is returned.

**Acceptance Scenarios**:

1. **Given** a configuration with `driver.mode = "claude"` and iflow not installed, **When** the gateway starts, **Then** no iflow-related errors or warnings appear, and the gateway becomes fully operational.
2. **Given** the gateway is running in claude mode, **When** a user sends a message via any channel, **Then** the response is generated via Claude Code with no iflow code path executed.
3. **Given** the gateway is running in claude mode, **When** `gateway status` is checked, **Then** the status reflects Claude Code as the active backend without mentioning iflow.

---

### User Story 2 - iflow Operation Unaffected (Priority: P2)

An operator who uses iflow as their backend should experience no change in behavior after the decoupling refactor. All three iflow modes (cli, stdio, acp) must continue to work exactly as before.

**Why this priority**: Decoupling must not regress existing iflow users. This validates backward compatibility.

**Independent Test**: Configure each of the three iflow modes (cli, stdio, acp) in turn, send messages, and confirm responses identical to pre-refactor behavior.

**Acceptance Scenarios**:

1. **Given** `driver.mode = "cli"`, **When** a message is sent, **Then** the iflow CLI subprocess is invoked and a valid response is returned.
2. **Given** `driver.mode = "stdio"`, **When** a message is sent, **Then** the long-running iflow process handles the message and streams the response correctly.
3. **Given** `driver.mode = "acp"`, **When** a message is sent, **Then** the WebSocket-based iflow ACP server handles the message correctly.
4. **Given** iflow is installed, **When** `gateway status` is checked, **Then** iflow-specific status information is shown only when an iflow mode is active.

---

### User Story 3 - Clean Configuration Per Backend (Priority: P3)

An operator should be able to configure the gateway with a config file that clearly separates backend-specific settings. iflow settings should not appear when only Claude Code is used, and vice versa.

**Why this priority**: Clean configuration reduces confusion and errors for operators who use only one backend.

**Independent Test**: Create a minimal config with only Claude Code settings, validate the config, and confirm no iflow-specific keys are required or flagged as unknown.

**Acceptance Scenarios**:

1. **Given** a config file with only Claude Code settings, **When** the gateway validates the config, **Then** no errors or warnings about missing iflow fields appear.
2. **Given** a config file with only iflow settings, **When** the gateway validates the config, **Then** no errors or warnings about missing Claude Code fields appear.
3. **Given** a config with an invalid backend name, **When** the gateway starts, **Then** a clear error identifies the unknown backend and lists valid options.

---

### User Story 4 - Backend Health Check Per Mode (Priority: P4)

An operator running `gateway status` should see health information relevant only to the configured backend, not checks for all possible backends.

**Why this priority**: Currently status checks run iflow-specific checks regardless of the configured mode, which produces misleading output when using Claude Code.

**Independent Test**: Set `driver.mode = "claude"`, run `gateway status`, and verify that only Claude Code readiness is checked with no iflow-related output.

**Acceptance Scenarios**:

1. **Given** `driver.mode = "claude"`, **When** `gateway status` runs, **Then** only Claude Code availability is checked and reported.
2. **Given** `driver.mode = "cli"` or `"stdio"` or `"acp"`, **When** `gateway status` runs, **Then** iflow availability is checked and reported.
3. **Given** the active backend is not installed, **When** `gateway status` runs, **Then** a clear, backend-specific error message tells the operator what is missing.

---

### Edge Cases

- What happens when an operator switches `driver.mode` from `claude` to `cli` without iflow installed? The gateway should give a clear error on next start.
- What happens when an operator's existing config uses the old mixed `driver` format? The gateway MUST emit a clear error identifying the old format and point to the migration guide; it MUST NOT silently ignore or auto-convert the config.
- What happens when a session was previously created with iflow and the mode is changed to Claude Code? The session mapping should gracefully start a new session for the new backend.
- What happens when the iflow binary is present but corrupted? The gateway should report a backend-specific health check failure, not crash.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST operate fully when `driver.mode` is `"claude"` without iflow being installed on the host.
- **FR-002**: System MUST preserve all existing behavior for `driver.mode` values `"cli"`, `"stdio"`, and `"acp"` (iflow modes).
- **FR-003**: System MUST isolate all iflow-specific logic (process invocation, protocol handling, session path resolution, workspace init) so it is only executed when an iflow mode is active. iflow protocol files (`acp.py`, `stdio_acp.py`) MUST remain in their current location; isolation is achieved via conditional guards at call sites, not file relocation.
- **FR-004**: System MUST provide backend-specific health checks: iflow readiness is only checked when an iflow mode is active; Claude Code readiness is only checked when `claude` mode is active.
- **FR-005**: Configuration MUST clearly separate iflow-specific settings from Claude Code-specific settings, with neither set required when only the other backend is used. This is a breaking change: the new config format is not backward compatible with the old mixed `driver` object; a migration guide MUST be provided and the system MUST emit a clear error when it detects an old-format config file.
- **FR-006**: System MUST NOT create iflow-specific workspace files (e.g., `.iflow/settings.json`) when operating in Claude Code mode.
- **FR-007**: Web UI session cookies and internal identifiers MUST be renamed to generic names (e.g., `cli_bridge_web_session`, `cli_bridge_console_token`). Hardcoded iflow path references (e.g., `~/.iflow/acp/sessions`) MUST be replaced with backend-agnostic equivalents. Web UI functional logic (session browsing behavior) is NOT changed in this feature.
- **FR-008**: The `iflow` passthrough CLI command MUST remain functional for operators who still use it. It MUST print a deprecation warning at runtime indicating it will be removed in a future version. It MUST be documented as iflow-specific and optional.
- **FR-009**: All existing tests MUST continue to pass after the refactor, and new tests MUST cover Claude-only scenarios.

### Key Entities

- **Backend**: An AI processing engine (iflow or Claude Code) that receives messages and returns responses. Has modes, configuration, health status, and session management.
- **DriverConfig**: The configuration block that selects and configures the active backend. Must cleanly partition settings per backend.
- **Session Mapping**: A record that maps a channel+chat identity to a backend session ID. Must work independently of which backend is active.
- **Backend Adapter**: The software component that wraps a specific backend. Must implement a common interface so the rest of the system is backend-agnostic.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A gateway configured with `driver.mode = "claude"` starts successfully on a system where iflow is not installed, producing zero iflow-related log output.
- **SC-002**: All three iflow modes (cli, stdio, acp) continue to handle messages correctly as verified by the existing test suite — zero test regressions.
- **SC-003**: A new minimal Claude-only configuration passes validation with no unknown or missing field warnings.
- **SC-004**: The `gateway status` command output contains only backend-relevant information for the active mode — no iflow checks when in Claude mode, no Claude checks when in iflow mode.
- **SC-005**: All iflow-specific identifiers (cookies, session path references, workspace init files) are removed from code paths that execute in Claude mode.
- **SC-006**: A new contributor can add a third backend by implementing one clearly defined interface and registering it, without modifying any existing channel or bus code.

## Assumptions

- The four existing driver modes (`cli`, `stdio`, `acp`, `claude`) represent the full set of modes in scope for this refactor. New backend types are a stretch goal but the design should accommodate them.
- iflow passthrough commands (`cli_bridge/cli/iflow_passthrough.py`) are retained but treated as an optional, iflow-specific CLI extension.
- Existing session mappings stored on disk do not need to be migrated; a new session is acceptable when the backend changes.
- The MCP proxy configuration is iflow-specific and MUST be moved into the iflow backend config block. It is NOT exposed in the Claude Code config. Generalizing MCP proxy support for Claude Code is out of scope and deferred to a future feature.

## Clarifications

### Session 2026-03-11

- Q: 重构后旧格式配置文件是否需要继续工作？ → A: 破坏性变更，要求迁移到新格式，提供迁移指南。
- Q: MCP 代理是否纳入本次重构范围并泛化？ → A: 隔离但不泛化，移入 iflow 专属配置块，Claude Code 侧不暴露，泛化延后。
- Q: Web UI 的改动范围是什么？ → A: 仅重命名 Cookie、标识符、硬编码 iflow 路径，Web UI 功能逻辑不变。
- Q: iflow ACP/stdio 协议文件如何组织？ → A: 原地保留，仅在调用入口处加条件判断，iflow 模式才加载，不移动文件。
- Q: iflow passthrough 命令的长期处置？ → A: 保留功能，但加废弃警告，表明将在未来版本移除。
