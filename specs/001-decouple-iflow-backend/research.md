# Research: Decouple iflow Backend

**Branch**: `001-decouple-iflow-backend` | **Date**: 2026-03-11

---

## 1. Config Schema — Flat vs Discriminated Union

**Decision**: Replace the flat `DriverConfig` with a two-level structure: shared fields at top level, backend-specific fields in nested `iflow: IFlowBackendConfig` and `claude: ClaudeBackendConfig` objects.

**Rationale**:
- Pydantic v2 discriminated unions require the discriminator field to live at the same level as the union; since `mode` is in `DriverConfig`, nested backend-specific objects work cleanly without a full discriminated union on the top-level config.
- Nested objects give immediate visual separation: operators see `driver.iflow.*` vs `driver.claude.*`.
- Avoids `getattr(config.driver, "claude_path", "claude")` defensive access scattered across commands.py — callers simply check `config.driver.iflow` or `config.driver.claude`.

**Alternatives considered**:
- Full discriminated union at `driver` level (`IFlowDriverConfig | ClaudeDriverConfig`): cleaner type safety but requires a larger schema rewrite and breaks any code that does `config.driver.timeout` on a shared field.
- Keep flat, add `model_validator` to emit errors for cross-mode field combinations: minimal change, but does not achieve visual separation in config files.

---

## 2. Migration Detection Strategy

**Decision**: Breaking change — detect legacy flat format in the config loader and raise `ConfigMigrationError` with a clear human-readable message before the gateway starts.

**Detection method**: If the raw `driver` dict loaded from disk contains any of the known legacy top-level iflow fields (`iflow_path`, `yolo`, `compression_trigger_tokens`, `claude_path`, `claude_model`, `mcp_proxy_enabled`), it is a legacy config. These fields no longer exist at the `driver` root in the new schema.

**Rationale**: Strict fail-fast detection prevents silent misconfiguration. The error message tells the operator exactly which legacy fields were found and links to the migration guide. Auto-migration was rejected because it risks silently discarding operator-customised values.

**Migration guide content** (to be created as `docs/config-migration-v2.md`):
- Show before/after JSON diff for each backend mode
- Note that `driver.model` becomes `driver.iflow.model` or `driver.claude.model` depending on mode

---

## 3. BaseAdapter Abstract Interface

**Decision**: Introduce `BaseAdapter(ABC)` in `cli_bridge/engine/base_adapter.py` with abstract methods covering the common contract shared by `IFlowAdapter` and `ClaudeAdapter`.

**Common interface identified from codebase analysis**:

```python
class BaseAdapter(ABC):
    mode: str  # abstract class attribute
    session_mappings: SessionMappingManager

    @abstractmethod
    async def chat(self, message, channel, chat_id, model, timeout) -> str: ...

    @abstractmethod
    async def chat_stream(self, message, channel, chat_id, model, timeout,
                          on_chunk, on_tool_call, on_event) -> str: ...

    @abstractmethod
    async def new_chat(self, message, channel, chat_id, model, timeout) -> str: ...

    @abstractmethod
    def clear_session(self, channel: str, chat_id: str) -> bool: ...

    @abstractmethod
    async def health_check(self) -> bool: ...

    @abstractmethod
    async def close(self) -> None: ...
```

**Rationale**: `AgentLoop` only calls `chat`, `chat_stream`, `new_chat`, `clear_session`, `health_check`, and `close`. Formalising this as an ABC makes the contract explicit and enables type-checked adapter selection. `IFlowAdapter.run_iflow_command()` and `list_iflow_sessions()` are iflow-specific extras that are NOT part of the base interface.

**Note on `clear_session`**: Currently `IFlowAdapter` delegates clear_session to mode-specific inner adapters via `AgentLoop` mode checks. Post-refactor, `IFlowAdapter.clear_session()` wraps this delegation internally so callers (AgentLoop) use a uniform `adapter.clear_session()` call regardless of mode.

---

## 4. Health Check Dispatch

**Decision**: Replace `check_iflow_installed()` / `ensure_iflow_ready()` with a generic `check_backend_ready(mode, config) -> tuple[bool, str]` dispatcher in `cli_bridge/cli/health.py`.

**Rationale**: Commands.py currently hardcodes iflow binary checks. The new function dispatches based on `mode`:
- `mode in {"cli", "stdio", "acp"}` → check `config.driver.iflow.iflow_path` binary
- `mode == "claude"` → check `config.driver.claude.claude_path` binary

**iflow auto-install via npm is retained** only when mode is an iflow mode; it is never triggered in claude mode.

---

## 5. Workspace Init Guard

**Decision**: In `init_workspace()`, gate the creation of `.iflow/settings.json` behind `mode != "claude"`. All markdown template files (AGENTS.md, BOOTSTRAP.md, etc.) are backend-agnostic and copied regardless of mode.

**Rationale**: The template markdown files describe agent behaviour expected by any backend. The `.iflow/settings.json` is specifically the iflow config that tells iflow which context files to load — it is meaningless in claude mode.

---

## 6. iflow Protocol Files (acp.py, stdio_acp.py)

**Decision**: Files remain in place at `cli_bridge/engine/`. Isolation is achieved by conditional import guards in `IFlowAdapter.__init__` — these modules are only imported when an iflow mode is active.

**Rationale**: Moving files would break existing imports and require a larger refactor. Lazy imports (or top-level conditional imports inside IFlowAdapter) achieve the same isolation without file relocation. Since `ClaudeAdapter` already lives in a separate file and is imported conditionally in commands.py, the same pattern applies to iflow modules.

---

## 7. MCP Proxy Scope

**Decision**: MCP proxy config fields (`mcp_proxy_*`, `mcp_servers_*`) move into `IFlowBackendConfig`. They are not exposed in `ClaudeBackendConfig`. MCP proxy functionality is unchanged.

**Rationale**: Per clarification Q2, generalizing MCP proxy for Claude Code is deferred. The proxy remains fully iflow-specific in this iteration.

---

## 8. Web UI Identifiers

**Decision**: Rename `iflow_web_session` → `cli_bridge_web_session`, `iflow_console_token` → `cli_bridge_console_token`. Replace the hardcoded `~/.iflow/acp/sessions` path reference with the session storage path returned by the active backend's configuration.

**Rationale**: Per clarification Q3, only renaming is in scope. No functional Web UI behaviour changes.

---

## 9. iflow Passthrough Deprecation

**Decision**: Add a deprecation warning printed to stderr when any `iflow` passthrough subcommand is invoked. Warning text: "Warning: The `iflow` passthrough command is deprecated and will be removed in a future version. Use iflow directly instead."

**Rationale**: Per clarification Q5, functionality is retained but users are notified of the planned removal.
