# Implementation Plan: Decouple iflow Backend for Multi-Backend Support

**Branch**: `001-decouple-iflow-backend` | **Date**: 2026-03-11 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/001-decouple-iflow-backend/spec.md`

---

## Summary

The gateway's core code currently mixes iflow-specific details (config fields, binary checks, session paths, workspace init, Web UI identifiers) with backend-agnostic logic, making it impossible to run in Claude Code mode without iflow installed. This plan decouples the two backends by:

1. Splitting the flat `DriverConfig` into nested `IFlowBackendConfig` / `ClaudeBackendConfig` objects (breaking config change with migration detection)
2. Introducing a `BaseAdapter` ABC to formalise the common adapter contract
3. Guarding all iflow-specific code paths (workspace init, health checks, Web UI paths) behind mode conditionals
4. Adding deprecation warnings to the iflow passthrough CLI command

After this plan, `driver.mode = "claude"` works fully without iflow installed, and all three iflow modes remain unchanged.

---

## Technical Context

**Language/Version**: Python 3.10+ (targets 3.10, 3.11, 3.12)
**Primary Dependencies**: Pydantic v2 (config), Typer (CLI), FastAPI + Uvicorn (Web UI), `claude-agent-sdk` (Claude backend), asyncio (concurrency), loguru (logging)
**Storage**: JSON config file at `~/.cli-bridge/config.json`; session mappings at `~/.cli-bridge/session_mappings.json`; workspace markdown files at configurable path
**Testing**: pytest with `asyncio_mode = "auto"`; ruff for lint/format
**Target Platform**: Linux server (primary); macOS and Windows supported
**Project Type**: CLI gateway service
**Performance Goals**: No new latency introduced — this is a structural refactor with no hot-path changes
**Constraints**: Config schema change must be breaking (per clarification Q1); ACP/stdio protocol files stay in place (per clarification Q4)
**Scale/Scope**: ~10 files modified; ~5 new files created; existing test suite must pass with zero regressions

---

## Constitution Check

*Constitution template is not yet fully filled in for this project. Applying general software quality principles:*

| Principle | Status |
|-----------|--------|
| Backward compatibility declared? | PASS — breaking change is explicit and documented with migration guide |
| Tests required? | PASS — FR-009 mandates existing tests pass + new Claude-only tests added |
| Isolation of concerns? | PASS — backend-specific code gated by mode, not scattered conditionals |
| No cross-backend leakage? | PASS — iflow fields only reachable via `driver.iflow.*`; claude fields via `driver.claude.*` |
| Simplicity (no premature abstraction)? | PASS — BaseAdapter ABC matches exactly the methods AgentLoop already calls; no new methods added |

**No violations requiring justification.**

---

## Project Structure

### Documentation (this feature)

```text
specs/001-decouple-iflow-backend/
├── spec.md              # Feature specification
├── plan.md              # This file
├── research.md          # Phase 0 research findings
├── data-model.md        # Config schema + entity design
├── contracts/
│   ├── backend-adapter.md   # BaseAdapter interface contract
│   └── config-schema.md     # Config v2 schema contract
└── tasks.md             # Phase 2 output (/speckit.tasks - NOT created by /speckit.plan)
```

### Source Code (files to be modified or created)

```text
cli_bridge/
├── config/
│   ├── schema.py               # MODIFY: split DriverConfig into nested backend configs
│   └── loader.py               # MODIFY: add ConfigMigrationError detection + DEFAULT_CONFIG update
│
├── engine/
│   ├── base_adapter.py         # CREATE: BaseAdapter ABC
│   ├── adapter.py              # MODIFY: IFlowAdapter implements BaseAdapter; add clear_session() wrapper
│   └── claude_adapter.py       # MODIFY: ClaudeAdapter implements BaseAdapter; verify close() exists
│
├── cli/
│   ├── commands.py             # MODIFY: health check dispatch; workspace init guard; adapter instantiation
│   ├── health.py               # CREATE: check_backend_ready(mode, config) dispatcher
│   └── iflow_passthrough.py    # MODIFY: add deprecation warning to all passthrough subcommands
│
└── web/
    └── server.py               # MODIFY: rename iflow_* cookies/identifiers; fix hardcoded iflow path

tests/
├── engine/
│   └── test_claude_only.py     # CREATE: tests for claude mode without iflow installed
├── config/
│   └── test_migration.py       # CREATE: tests for ConfigMigrationError detection
└── cli/
    └── test_health_dispatch.py # CREATE: tests for backend health check dispatch
```

**Structure Decision**: Single project layout — no restructuring of existing directories. New files are additive; existing files are modified in-place. Protocol files (`acp.py`, `stdio_acp.py`) are not moved per clarification Q4.

---

## Implementation Phases

### Phase A: Config Schema Refactor

**Goal**: Split `DriverConfig` and add migration detection.

**Files**:
- `cli_bridge/config/schema.py`: Create `IFlowBackendConfig`, `ClaudeBackendConfig`; revise `DriverConfig` with nested fields and `model_validator`
- `cli_bridge/config/loader.py`: Add `_check_for_legacy_config()` raising `ConfigMigrationError`; update `DEFAULT_CONFIG` template to v2 format

**Key design**:
```python
class DriverConfig(BaseModel):
    mode: Literal["cli", "stdio", "acp", "claude"] = "stdio"
    max_turns: int = 40
    timeout: int = DEFAULT_TIMEOUT
    workspace: str = ""
    iflow: Optional[IFlowBackendConfig] = None
    claude: Optional[ClaudeBackendConfig] = None

    @model_validator(mode="after")
    def populate_backend_config(self) -> Self:
        if self.mode in ("cli", "stdio", "acp") and self.iflow is None:
            self.iflow = IFlowBackendConfig()
        elif self.mode == "claude" and self.claude is None:
            self.claude = ClaudeBackendConfig()
        return self
```

**Config.get_model()** updated to read from the appropriate nested config based on mode.

---

### Phase B: BaseAdapter Abstract Class

**Goal**: Formalise the adapter interface.

**Files**:
- `cli_bridge/engine/base_adapter.py` (new): `BaseAdapter(ABC)` with abstract methods

**IFlowAdapter update**:
- Declare `class IFlowAdapter(BaseAdapter)`
- Add `clear_session(channel, chat_id) -> bool` at meta level (wraps inner adapter logic currently in AgentLoop)
- Remove the mode-switch `clear_session` logic from `loop.py` (move it into `IFlowAdapter`)

**ClaudeAdapter update**:
- Declare `class ClaudeAdapter(BaseAdapter)`
- Verify `close()` is implemented; add if missing

---

### Phase C: Health Check Dispatch

**Goal**: Replace iflow-hardcoded health checks with mode-aware dispatch.

**Files**:
- `cli_bridge/cli/health.py` (new):
  ```python
  async def check_backend_ready(config: DriverConfig) -> tuple[bool, str]:
      if config.mode == "claude":
          return await _check_claude_ready(config.claude.claude_path)
      else:
          return await _check_iflow_ready(config.iflow.iflow_path)
  ```
- `cli_bridge/cli/commands.py`: Replace all `check_iflow_installed()` / `ensure_iflow_ready()` calls with `check_backend_ready(config)`

---

### Phase D: Adapter Instantiation Update

**Goal**: Use new config accessors in the adapter factory.

**File**: `cli_bridge/cli/commands.py` (adapter creation block)

**Before**:
```python
adapter = ClaudeAdapter(
    claude_path=getattr(config.driver, "claude_path", "claude"),
    ...
)
```

**After**:
```python
adapter = ClaudeAdapter(
    claude_path=config.driver.claude.claude_path,
    model=config.driver.claude.model,
    permission_mode=config.driver.claude.permission_mode,
    system_prompt=config.driver.claude.system_prompt,
    ...
)
```

Same pattern for `IFlowAdapter` using `config.driver.iflow.*`.

---

### Phase E: Workspace Init Guard

**Goal**: Skip `.iflow/settings.json` creation in claude mode.

**File**: `cli_bridge/cli/commands.py` (`init_workspace()`)

```python
if config.driver.mode in ("cli", "stdio", "acp"):
    # create .iflow/settings.json
    ...
# else: skip — claude mode does not use iflow settings
```

---

### Phase F: Web UI Rename

**Goal**: Replace iflow-specific identifiers in Web UI.

**File**: `cli_bridge/web/server.py`

| Old | New |
|-----|-----|
| `iflow_web_session` | `cli_bridge_web_session` |
| `iflow_console_token` | `cli_bridge_console_token` |
| `~/.iflow/acp/sessions` | `config.driver.iflow.acp_session_dir` (resolved from iflow config) |

---

### Phase G: iflow Passthrough Deprecation

**Goal**: Add deprecation warning.

**File**: `cli_bridge/cli/iflow_passthrough.py`

Add to the passthrough command entry point:
```python
import warnings
warnings.warn(
    "The `iflow` passthrough command is deprecated and will be removed in a future version. "
    "Use iflow directly instead.",
    DeprecationWarning,
    stacklevel=2,
)
```

Or emit via loguru to stderr for consistency with the rest of the codebase.

---

### Phase H: Tests

**Goal**: Add coverage for new Claude-only paths and migration detection.

**New test files**:

1. `tests/config/test_migration.py`
   - Test that loading a v1 config raises `ConfigMigrationError`
   - Test that loading a v2 iflow config succeeds
   - Test that loading a v2 claude config succeeds with no iflow fields required

2. `tests/engine/test_claude_only.py`
   - Test `ClaudeAdapter` can be instantiated and `health_check()` returns False gracefully when claude binary not found (no iflow code path executed)
   - Test `AgentLoop` with `ClaudeAdapter` processes a message without importing iflow modules

3. `tests/cli/test_health_dispatch.py`
   - Test `check_backend_ready` dispatches to claude check when mode = "claude"
   - Test `check_backend_ready` dispatches to iflow check when mode ∈ {cli, stdio, acp}

---

## Complexity Tracking

No violations requiring justification.
