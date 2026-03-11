# Tasks: Decouple iflow Backend for Multi-Backend Support

**Input**: Design documents from `specs/001-decouple-iflow-backend/`
**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/ ✓

**Tests**: Included — FR-009 mandates existing tests pass + new Claude-only coverage.

**Organization**: Tasks grouped by user story for independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this belongs to (US1=P1, US2=P2, US3=P3, US4=P4)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Config schema split and BaseAdapter ABC — MUST complete before any user story work.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T001 [P] Create `IFlowBackendConfig` and `ClaudeBackendConfig` Pydantic model classes in `cli_bridge/config/schema.py` with all fields from `data-model.md` (iflow fields: iflow_path, model, yolo, thinking, extra_args, compression_trigger_tokens, acp_host, acp_port, disable_mcp, mcp_proxy_*, mcp_servers_*; claude fields: claude_path, model, system_prompt, permission_mode)
- [X] T002 Revise `DriverConfig` in `cli_bridge/config/schema.py` to remove all flat backend-specific fields, add `iflow: Optional[IFlowBackendConfig] = None` and `claude: Optional[ClaudeBackendConfig] = None`, add `@model_validator(mode="after")` to auto-populate the relevant nested config based on `mode` (depends on T001)
- [X] T003 Update `Config.get_model()` in `cli_bridge/config/schema.py` to dispatch based on mode: return `driver.iflow.model` when mode ∈ {cli, stdio, acp}, return `driver.claude.model` when mode == "claude" (depends on T002)
- [X] T004 Add `ConfigMigrationError(ValueError)` class and `_check_for_legacy_config(raw_driver: dict)` function to `cli_bridge/config/loader.py`; raise with message listing detected legacy fields and referencing `docs/config-migration-v2.md`; call in `load_config()` before Pydantic parsing (depends on T001)
- [X] T005 Update `DEFAULT_CONFIG` dict in `cli_bridge/config/loader.py` to v2 nested format: move all iflow fields under `"iflow": {...}` key inside `"driver"`, keep shared fields (mode, max_turns, timeout, workspace) at driver root, remove all claude_* fields from default (depends on T002)
- [X] T006 [P] Create `cli_bridge/engine/base_adapter.py` with `BaseAdapter(ABC)` class containing abstract methods: `chat()`, `chat_stream()`, `new_chat()`, `clear_session()`, `health_check()`, `close()` and abstract property `mode`; method signatures must match `contracts/backend-adapter.md` exactly

**Checkpoint**: Config schema v2 is in place and BaseAdapter contract is defined — user story work can now begin.

---

## Phase 3: User Story 1 — Claude Code Exclusive Operation (Priority: P1) 🎯 MVP

**Goal**: Gateway runs fully in claude mode without iflow installed — zero iflow code paths executed.

**Independent Test**: Set `driver.mode = "claude"` in config, ensure iflow binary is absent, run `uv run cli-bridge gateway run` and send a message via any channel; confirm response returned with no iflow errors in logs.

### Implementation for User Story 1

- [X] T007 [P] [US1] Update `ClaudeAdapter` in `cli_bridge/engine/claude_adapter.py` to extend `BaseAdapter`; add `async def close(self) -> None` if missing; ensure `clear_session()` is implemented; update class declaration to `class ClaudeAdapter(BaseAdapter)`
- [X] T008 [P] [US1] Update adapter instantiation block in `cli_bridge/cli/commands.py` (claude mode branch): replace all `getattr(config.driver, "claude_path", ...)` with `config.driver.claude.claude_path`, `config.driver.claude.model`, `config.driver.claude.permission_mode`, `config.driver.claude.system_prompt`; remove all `getattr` defensive fallbacks for claude fields
- [X] T009 [US1] Add mode guard in `init_workspace()` in `cli_bridge/cli/commands.py`: wrap the `.iflow/settings.json` creation block with `if config.driver.mode in ("cli", "stdio", "acp"):` so it is skipped entirely when `mode == "claude"` (depends on T002)
- [X] T010 [P] [US1] Rename Web UI identifiers in `cli_bridge/web/server.py`: replace `iflow_web_session` → `cli_bridge_web_session`, `iflow_console_token` → `cli_bridge_console_token`; replace hardcoded `Path.home() / ".iflow" / "acp" / "sessions"` with `Path(config.driver.iflow.acp_session_dir) if config.driver.iflow else Path(workspace) / "sessions"` (or equivalent safe path)
- [X] T011 [US1] Write `tests/config/test_migration.py`: test that loading a v1 flat config dict raises `ConfigMigrationError`; test that loading a v2 claude-mode config dict succeeds with no iflow fields required; test that `config.driver.claude` is populated and `config.driver.iflow` is None when mode == "claude"
- [X] T012 [US1] Write `tests/engine/test_claude_only.py`: test that `ClaudeAdapter` can be instantiated with a v2 claude config; test that `ClaudeAdapter.health_check()` returns `False` gracefully when claude binary is not found (mock subprocess); confirm no iflow modules are imported during instantiation (use `sys.modules` assertion)

**Checkpoint**: User Story 1 fully functional — claude mode works without iflow.

---

## Phase 4: User Story 2 — iflow Operation Unaffected (Priority: P2)

**Goal**: All three iflow modes (cli, stdio, acp) behave identically to pre-refactor — zero regressions.

**Independent Test**: Configure each of mode=cli, mode=stdio, mode=acp; send a message; confirm response returned. Run `uv run pytest tests/ -v` and confirm all pre-existing tests pass.

### Implementation for User Story 2

- [X] T013 [US2] Update `IFlowAdapter` in `cli_bridge/engine/adapter.py` to extend `BaseAdapter`; implement `clear_session(channel, chat_id) -> bool` at the meta level (wraps inner adapter delegation logic currently spread in `loop.py`); update class declaration to `class IFlowAdapter(BaseAdapter)` (depends on T006)
- [X] T014 [P] [US2] Update `IFlowAdapter` instantiation block in `cli_bridge/cli/commands.py` (iflow mode branch): replace all `getattr(config.driver, "iflow_path", ...)` with `config.driver.iflow.iflow_path`; replace `config.get_model()` → `config.driver.iflow.model`; replace all iflow-specific getattr calls with direct `config.driver.iflow.*` access (depends on T002)
- [X] T015 [US2] Update `AgentLoop` in `cli_bridge/engine/loop.py`: remove the mode-switch `clear_session` routing block (lines 255–270) and replace with a single `cleared = self.adapter.clear_session(msg.channel, msg.chat_id)` call, relying on each adapter's `BaseAdapter.clear_session()` implementation (depends on T013)
- [X] T016 [US2] Run full existing test suite `uv run pytest tests/ -v` and fix any failures caused by the config schema or adapter changes; all pre-existing tests must pass before this task is complete

**Checkpoint**: User Stories 1 AND 2 both work independently — existing iflow functionality preserved.

---

## Phase 5: User Story 3 — Clean Configuration Per Backend (Priority: P3)

**Goal**: A minimal claude-only config passes validation with no iflow field errors; a minimal iflow-only config passes with no claude field errors.

**Independent Test**: Create a config JSON with only `driver.mode = "claude"` and `driver.claude` fields; run `uv run cli-bridge gateway run` in dry-run/status mode; confirm no warnings about missing iflow fields.

### Implementation for User Story 3

- [X] T017 [P] [US3] Add a `_build_claude_default_config()` helper or add a claude-mode branch to the config initialization in `cli_bridge/config/loader.py` so that when the gateway is first run with `mode = "claude"` the auto-created `~/.cli-bridge/config.json` only contains `driver.claude` (no `driver.iflow` block) (depends on T005)
- [X] T018 [P] [US3] Write migration guide `docs/config-migration-v2.md`: show side-by-side JSON diff for v1 → v2 for each mode (stdio/cli/acp/claude); include the exact `ConfigMigrationError` message that operators will see; include manual migration steps
- [X] T019 [US3] Extend `tests/config/test_migration.py`: add tests that an iflow-only v2 config loads with `driver.claude is None`; add test that a claude-only v2 config loads with `driver.iflow is None`; add test that an invalid mode value raises Pydantic validation error with clear message listing valid options

**Checkpoint**: Config cleanly partitioned — operators see only their backend's settings.

---

## Phase 6: User Story 4 — Backend Health Check Per Mode (Priority: P4)

**Goal**: `gateway status` shows only backend-relevant checks — no iflow checks in claude mode.

**Independent Test**: Set `mode = "claude"`, run `uv run cli-bridge gateway status`; confirm output contains only Claude availability check; confirm no iflow binary check is executed.

### Implementation for User Story 4

- [X] T020 [US4] Create `cli_bridge/cli/health.py`: implement `async def check_backend_ready(mode: str, config: DriverConfig) -> tuple[bool, str]` that dispatches to `_check_claude_ready(claude_path)` when mode == "claude" or `_check_iflow_ready(iflow_path)` when mode ∈ {cli, stdio, acp}; retain iflow npm auto-install logic inside `_check_iflow_ready` only (depends on T002)
- [X] T021 [US4] Update `cli_bridge/cli/commands.py`: replace all `check_iflow_installed()`, `ensure_iflow_ready()`, `check_iflow_logged_in()` call sites with `await check_backend_ready(config.driver.mode, config)`; update the `gateway status` display to show backend-mode-specific fields only (no mcp_proxy_enabled when mode == "claude") (depends on T020)
- [X] T022 [P] [US4] Write `tests/cli/test_health_dispatch.py`: test that `check_backend_ready` with mode="claude" calls `_check_claude_ready` and NOT `_check_iflow_ready`; test that mode="cli" calls `_check_iflow_ready` and NOT `_check_claude_ready`; mock both check functions to verify dispatch

**Checkpoint**: All four user stories are independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Deprecation warning, status display cleanup, final validation.

- [X] T023 [P] Add deprecation warning to `cli_bridge/cli/iflow_passthrough.py` at the entry point of all passthrough subcommands: emit via `loguru.logger.warning("The 'iflow' passthrough command is deprecated and will be removed in a future version. Use iflow directly instead.")` before forwarding the command
- [X] T024 [P] Review and update `cli_bridge/cli/commands.py` gateway status display: ensure `model`, `thinking`, `yolo`, `mcp_proxy_*` fields are only shown when the active mode is an iflow mode; show `claude.model`, `claude.permission_mode` only when mode == "claude"
- [X] T025 Run complete test suite `uv run pytest tests/ -v` and lint `uv run ruff check cli_bridge tests` to confirm zero regressions and no lint errors; fix any remaining issues

---

## Dependencies & Execution Order

### Phase Dependencies

- **Foundational (Phase 2)**: No dependencies — start immediately
- **US1 (Phase 3)**: Depends on T001, T002, T006 from Foundational
- **US2 (Phase 4)**: Depends on T001, T002, T006 from Foundational; T015 depends on T013
- **US3 (Phase 5)**: Depends on T002, T005 from Foundational
- **US4 (Phase 6)**: Depends on T002 from Foundational; T021 depends on T020
- **Polish (Phase 7)**: Depends on all user story phases complete

### User Story Dependencies

- **US1 (P1)**: Depends on Foundational only — no dependency on US2/US3/US4
- **US2 (P2)**: Depends on Foundational only — T013 adds `clear_session()` to IFlowAdapter
- **US3 (P3)**: Depends on Foundational only — config partitioning work
- **US4 (P4)**: Depends on Foundational only — health dispatch work
- US1 and US2 share `commands.py` edits — serialize T008/T009 and T014 to avoid conflicts

### Within Each User Story

- Schema classes (T001) → DriverConfig revision (T002) → accessors (T003, T005)
- BaseAdapter (T006) → ClaudeAdapter update (T007) → IFlowAdapter update (T013)
- All tests can be written once the implementations they test are complete

### Parallel Opportunities

- T001 and T006 are fully parallel (schema.py vs base_adapter.py)
- T007 and T008 and T010 are fully parallel within US1 (different files)
- T014 and T013 are fully parallel within US2 (adapter.py vs commands.py instantiation)
- T017 and T018 are fully parallel within US3 (loader.py vs docs/)
- T020 (health.py) and T022 (tests) can be written in parallel once T020 is done

---

## Parallel Example: Foundational Phase

```bash
# These two tasks touch different files — run together:
Task T001: "Create IFlowBackendConfig + ClaudeBackendConfig in schema.py"
Task T006: "Create BaseAdapter ABC in base_adapter.py"
```

## Parallel Example: User Story 1

```bash
# After T002 and T006 complete, these are parallel:
Task T007: "ClaudeAdapter extends BaseAdapter in claude_adapter.py"
Task T008: "commands.py claude instantiation uses config.driver.claude.*"
Task T010: "Rename Web UI identifiers in server.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 2: Foundational (T001–T006)
2. Complete Phase 3: User Story 1 (T007–T012)
3. **STOP and VALIDATE**: Run `uv run pytest tests/config/ tests/engine/test_claude_only.py -v`
4. Manual smoke test: start gateway with `mode = "claude"`, confirm no iflow errors

### Incremental Delivery

1. Foundational → config split + BaseAdapter → foundation ready
2. US1 (T007–T012) → Claude mode works without iflow → **MVP delivered**
3. US2 (T013–T016) → iflow modes verified unaffected → backward compat confirmed
4. US3 (T017–T019) → clean per-backend config → operators can use minimal configs
5. US4 (T020–T022) → mode-aware health checks → status command clean
6. Polish (T023–T025) → deprecation warnings, lint pass

---

## Notes

- [P] tasks = different files, no blocking dependencies — safe to parallelize
- `commands.py` is touched by US1 (T008, T009) and US2 (T014) and US4 (T021) — these MUST be serialized to avoid conflicts
- Commit after each task ID or logical group
- The `getattr()` defensive fallbacks in commands.py are a code smell; the whole point of the schema refactor is to eliminate them
- After T002, run `uv run pytest` immediately to catch any Pydantic schema errors before proceeding
