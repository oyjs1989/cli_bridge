# Research: Split Driver Config — Backend + Transport

**Branch**: `002-split-driver-config` | **Date**: 2026-03-11

## Decision Log

### 1. How to accept legacy `mode` while introducing `backend` + `transport`

**Decision**: Use Pydantic `model_validator(mode="before")` to intercept the raw dict before field assignment. If `mode` key is present and `backend`/`transport` are absent, pop `mode` and inject the derived `backend`/`transport` values.

**Rationale**: A `before` validator receives the raw input dict before any field coercion, making it the safest place to do the migration. No monkey-patching, no extra loader logic. The transformation is entirely within the schema layer where it belongs.

**Alternatives considered**:
- Add `mode` as an `Optional` Pydantic field alongside `backend`/`transport` and migrate in `model_validator(mode="after")` — rejected because having both `mode` field and `backend`/`transport` fields at the same time is confusing and pollutes `model_dump()` output
- Do the migration in `loader.py`'s `load_config()` function — rejected because the schema should own its own migration; callers creating `DriverConfig(mode="stdio")` directly (e.g., in tests) would not benefit

---

### 2. Backward-compat `mode` property

**Decision**: Add a `@property mode(self) -> str` to `DriverConfig` that derives the legacy string from `backend`+`transport`:
- `(iflow, cli)` → `"cli"`
- `(iflow, stdio)` → `"stdio"`
- `(iflow, acp)` → `"acp"`
- `(claude, cli)` → `"claude"`
- `(claude, stdio)` → `"claude"` (returns `"claude"` for any claude transport)

**Rationale**: Most call sites (commands.py, health.py, loop.py) check `if mode == "claude"` or `if mode in ("cli", "stdio", "acp")`. The property returns values that make those checks continue to work without touching every call site. Call sites that need to distinguish `claude+cli` from `claude+stdio` must read `backend`/`transport` directly.

**Alternatives considered**:
- Remove `mode` entirely and update every call site — rejected for scope reasons; would cause a large diff touching many tests and commands
- Keep `mode` as a regular Pydantic field — rejected because it creates ambiguity when both `mode` and `backend`/`transport` are present in a config

---

### 3. Where to validate unsupported combinations

**Decision**: Add a second `model_validator(mode="after")` in `DriverConfig` that raises `ValueError` if `backend="claude"` and `transport="acp"`. This is separate from the auto-population validator.

**Rationale**: Fail fast at config-load time rather than at runtime when the gateway tries to connect. Config validation is the right place for "not yet supported" checks.

**Alternatives considered**:
- Validate in `commands.py` at startup — rejected because `DriverConfig` created programmatically (e.g. in tests) would bypass the check

---

### 4. Default values when `backend`/`transport` are omitted

**Decision**:
- Default `backend = "iflow"`, default `transport = "stdio"` — matches current default `mode = "stdio"`
- When only `backend = "claude"` is specified with no `transport`, default `transport = "cli"`

**Rationale**: Preserves existing default behaviour. The `transport` default for claude is `cli` because that is the currently-implemented combination.

**Implementation note**: Since `backend` and `transport` have default values, the `before` validator only needs to act when `mode` key is present. If neither `mode` nor `backend`/`transport` are in the dict, Pydantic uses the field defaults.

---

### 5. `IFlowAdapter.mode` → `IFlowAdapter.transport`

**Decision**: Rename internal field `_mode` to `_transport` and expose `transport` property. Keep `mode` as a deprecated alias property returning `self._transport`.

**Rationale**: `IFlowAdapter` only ever sees iflow transports (cli/stdio/acp) — never "claude". So renaming to `transport` is semantically accurate. The `mode` alias allows `loop.py`'s `getattr(self.adapter, "mode", "cli")` to keep working until loop.py is updated.

**Alternatives considered**:
- Update all `getattr(self.adapter, "mode", ...)` call sites immediately — accepted as part of this PR since it's only 1 reference in loop.py

---

### 6. `claude+stdio` adapter implementation

**Decision**: Create a new `ClaudeStdioAdapter` in `cli_bridge/engine/claude_stdio_adapter.py` that manages a persistent `claude --output-format stream-json --verbose` subprocess, reusing it across messages via session management. The adapter mirrors `StdioACPAdapter` patterns for iflow.

**Rationale**: The claude-agent-sdk `query()` function spawns a new subprocess per call. A persistent process adapter needs direct subprocess management, analogous to `StdioACPAdapter` for iflow.

**Alternatives considered**:
- Extend `ClaudeAdapter` with a `transport` parameter and branch internally — rejected as it would make `ClaudeAdapter` too complex; a separate class is cleaner and independently testable
- Reuse `StdioACPAdapter` directly for claude — rejected because the protocol (iflow's experimental-acp vs claude's JSONL) is different

---

### 7. `loop.py` `inline_agents` check

**Decision**: Change from `getattr(self.adapter, "mode", "cli") not in {"stdio", "acp", "claude"}` to checking the new `inline_agents` property on `BaseAdapter`. Add `inline_agents: bool = True` to `BaseAdapter`, override to `False` in `StdioACPAdapter`, `ACPAdapter`, `ClaudeAdapter`, and `ClaudeStdioAdapter`.

**Rationale**: The current string-matching check is fragile and doesn't scale with new adapter types. A protocol-level property makes the behaviour explicit and maintainable.

**Alternatives considered**:
- Keep the string check and add `"claude-stdio"` to the set — rejected because it couples loop.py to adapter internals; every new adapter would require updating loop.py

---

### 8. `init_workspace()` in `commands.py`

**Decision**: Change signature from `init_workspace(workspace, mode)` to `init_workspace(workspace, backend)`. Update the check from `if mode in ("cli", "stdio", "acp")` to `if backend == "iflow"` (create `.iflow/settings.json` only for iflow backend).

**Rationale**: The check is logically "is this an iflow workspace?" — using `backend` makes that intent clear. The implementation is simpler (one value check instead of a set check).

---

### 9. `check_backend_ready()` in `health.py`

**Decision**: Change parameter from `mode: str` to `backend: str`. Update callers to pass `config.driver.backend`. Since the existing `mode` property returns "claude" for both claude transports, callers that haven't been updated yet will also continue to work (because `check_backend_ready("claude", ...)` still dispatches correctly).

**Rationale**: Health check cares only about which binary to check, not how to communicate with it.

---

### 10. Config file format — default config generator

**Decision**: `_create_default_config()` and `_build_*_driver_block()` in `loader.py` updated to write `backend`+`transport` instead of `mode`. New default configs written with the new format. Old configs with `mode` continue to work via in-memory migration.

**Rationale**: New installations should use the clean format. Old installations stay as-is until the user manually updates.
