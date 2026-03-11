# Tasks: Split Driver Config — Backend + Transport

**Input**: Design documents from `/specs/002-split-driver-config/`
**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/ ✓

**Organization**: Tasks grouped by user story (US1=backward compat, US2=independent dispatch, US3=claude+stdio).
No tests are explicitly requested in the spec — test tasks are included only where they verify critical migration correctness.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no sequential dependency on other in-flight tasks)
- **[Story]**: User story this task serves (US1/US2/US3 per spec.md priorities)

---

## Phase 1: Setup (New File Skeleton)

**Purpose**: Create the one new file this feature introduces so later phases have a concrete target to implement against.

- [X] T001 Create stub `cli_bridge/engine/claude_stdio_adapter.py` with empty `ClaudeStdioAdapter(BaseAdapter)` class, placeholder `connect()`, `stop()`, `chat()`, `chat_stream()`, `clear_session()` methods, and `transport = "stdio"` / `inline_agents = False` properties

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core schema and adapter-base changes that every other task depends on. **No user story work can begin until this phase is complete.**

- [X] T002 Update `cli_bridge/config/schema.py`: (a) replace `mode: Literal["cli","acp","stdio","claude"]` field with `backend: Literal["iflow","claude"] = "iflow"` and `transport: Literal["cli","stdio","acp"] = "stdio"`; (b) add `model_validator(mode="before")` classmethod `migrate_legacy_mode` that pops `mode` key and injects derived `backend`/`transport` per the mapping in `data-model.md`; (c) add `validate_combination` `model_validator(mode="after")` that raises `ValueError` when `backend="claude"` and `transport="acp"`; (d) update `populate_backend_config` validator to check `self.backend` instead of `self.mode`; (e) add `@property mode(self) -> str` computing the legacy string (see `data-model.md` derived-property table); (f) update `Config.get_model()` and `Config.get_workspace()` to branch on `self.driver.backend` instead of `self.driver.mode`

- [X] T003 [P] Update `cli_bridge/engine/base_adapter.py`: add `@property inline_agents(self) -> bool` returning `True` by default (override to `False` in long-lived adapters)

**Checkpoint**: `DriverConfig(mode="claude")`, `DriverConfig(mode="stdio")`, and `DriverConfig(backend="iflow", transport="cli")` all construct correctly. `driver.mode` property returns correct legacy string for all combinations.

---

## Phase 3: User Story 1 — Upgrade Without Config Changes (Priority: P1) 🎯 MVP

**Goal**: Existing operators with `driver.mode` in their config file can run the new version with zero changes to their `~/.cli-bridge/config.json`.

**Independent Test**: Start the gateway with a config file containing `"driver": {"mode": "claude", ...}` and confirm it routes to ClaudeAdapter correctly; repeat for `"mode": "stdio"`, `"mode": "cli"`, `"mode": "acp"`.

### Implementation for User Story 1

- [X] T004 [US1] Update `cli_bridge/config/loader.py`: (a) update `_build_iflow_driver_block(transport="stdio")` to write `backend`/`transport` keys instead of `mode`; (b) update `_build_claude_driver_block()` to write `backend="claude"`, `transport="cli"` instead of `mode="claude"`; (c) update `_create_default_config(config_path, mode)` to accept `backend`/`transport` params (keep `mode` param for caller backward compat by deriving backend/transport from it)

- [X] T005 [P] [US1] Update `tests/config/test_migration.py`: (a) add 4 test cases verifying each legacy `mode` value (`cli`, `stdio`, `acp`, `claude`) migrates to the correct `backend`+`transport`; (b) add test that `DriverConfig(backend="claude", transport="acp")` raises `ValueError`; (c) add test that new-format config (with `backend`/`transport`, no `mode`) loads correctly; (d) update existing assertions like `assert config.driver.mode == "claude"` to also/alternatively assert `config.driver.backend == "claude"` and `config.driver.transport == "cli"`; (e) update `DriverConfig(mode=...)` constructions to use the migrated form

**Checkpoint**: All tests in `tests/config/test_migration.py` pass. A config with `"mode": "claude"` loads as `backend="claude"`, `transport="cli"`.

---

## Phase 4: User Story 2 — Configure Backend and Transport Independently (Priority: P2)

**Goal**: All dispatch logic (adapter selection, health checks, workspace init, status display) uses `backend` and `transport` fields rather than the legacy `mode` string.

**Independent Test**: For each valid combination in the matrix (iflow+cli, iflow+stdio, iflow+acp, claude+cli), configure the gateway and confirm the correct adapter class is instantiated and `cli-bridge status` shows `Backend`/`Transport` not just `Mode`.

### Implementation for User Story 2

- [X] T006 [US2] Update `cli_bridge/engine/adapter.py` (`IFlowAdapter`): (a) rename constructor param `mode` → `transport`; (b) rename `self._mode` → `self._transport`; (c) rename `mode` property → `transport` property; (d) add `mode` as deprecated alias property returning `self._transport`; (e) override `inline_agents` property to return `self._transport == "cli"` (True only for cli transport, where AGENTS.md must be injected per message); (f) update `clear_session` dispatch to check `self._transport` instead of `self._mode`

- [X] T007 [P] [US2] Update `cli_bridge/engine/claude_adapter.py` (`ClaudeAdapter`): (a) add `@property transport(self) -> str` returning `"cli"`; (b) override `@property inline_agents(self) -> bool` returning `False`

- [X] T008 [P] [US2] Update `cli_bridge/cli/health.py`: (a) rename `check_backend_ready(mode, driver)` parameter to `check_backend_ready(backend, driver)`; (b) update the dispatch: `if backend == "claude":` (was `if mode == "claude":`); update docstring accordingly

- [X] T009 [US2] Update `cli_bridge/cli/commands.py`: (a) update `init_workspace(workspace, mode)` signature to `init_workspace(workspace, backend)` and change the inner check from `if mode in ("cli", "stdio", "acp")` to `if backend == "iflow"`; (b) in `_run_gateway_async`: replace `_mode = config.driver.mode` with `_backend = config.driver.backend` and `_transport = config.driver.transport`; (c) update MCP proxy check: `if _mode != "claude"` → `if _backend != "claude"`; (d) update ACP server start: `if mode == "acp"` → `if _transport == "acp"` and `mode = "cli"` fallback → `_transport = "cli"`; (e) update adapter creation: `if mode == "claude":` → `if _backend == "claude":` (creating ClaudeAdapter), else IFlowAdapter with `transport=_transport`; (f) update `init_workspace` call to pass `backend=_backend`; (g) in `gateway_run` status check: `_mode = config.driver.mode` → `_backend = config.driver.backend`; (h) update status display to print `Backend` and `Transport` instead of `Mode`; (i) update all calls to `check_backend_ready(_mode, ...)` → `check_backend_ready(_backend, ...)`

- [X] T010 [P] [US2] Update `cli_bridge/engine/loop.py`: replace `getattr(self.adapter, "mode", "cli") not in {"stdio", "acp", "claude"}` with `getattr(self.adapter, "inline_agents", True)` in `_get_bootstrap_content()`

- [X] T011 [P] [US2] Update `tests/cli/test_health_dispatch.py`: update any test that passes `mode` to `check_backend_ready` to pass `backend` instead; add test for `backend="iflow"` → dispatches to iflow checker; add test for `backend="claude"` → dispatches to claude checker

**Checkpoint**: Running `uv run pytest` passes all existing tests. A gateway started with `backend="iflow", transport="stdio"` config behaves identically to the old `mode="stdio"` config.

---

## Phase 5: User Story 3 — Claude as Long-Lived Process (Priority: P3)

**Goal**: `backend="claude"` and `transport="stdio"` creates a persistent Claude process that handles multiple messages without respawning.

**Independent Test**: Configure `backend="claude", transport="stdio"`, send 3 messages, confirm only one Claude process is spawned (visible in process list), and all 3 messages receive responses.

### Implementation for User Story 3

- [X] T012 [US3] Implement `ClaudeStdioAdapter` in `cli_bridge/engine/claude_stdio_adapter.py`: (a) constructor accepting `claude_path`, `model`, `workspace`, `permission_mode`, `system_prompt`, `max_turns`, `timeout`; (b) `connect()` method that spawns persistent `claude --output-format stream-json --verbose` subprocess and stores it; (c) `chat(message, channel, chat_id, model)` method that sends message to the persistent process and awaits response, managing session via `SessionMappingManager`; (d) `chat_stream(message, channel, chat_id, model, on_chunk, on_tool_call)` method for streaming responses; (e) `clear_session(channel, chat_id)` to remove session mapping; (f) `stop()` to gracefully terminate the subprocess; (g) reconnect logic if the process dies unexpectedly; (h) `transport = "stdio"` and `inline_agents = False` properties

- [X] T013 [US3] Update `cli_bridge/cli/commands.py` adapter creation block: add `elif _backend == "claude" and _transport == "stdio":` branch that imports and instantiates `ClaudeStdioAdapter`; call `await adapter.connect()` before the main loop starts (for stdio transports)

- [X] T014 [P] [US3] Create `tests/engine/test_claude_stdio_adapter.py`: (a) test `transport` property returns `"stdio"`; (b) test `inline_agents` returns `False`; (c) test `clear_session` removes mapping; (d) test that constructor accepts expected parameters; (e) test reconnect logic (mock subprocess death); use `FakeAdapter` patterns from existing `tests/engine/` test files

**Checkpoint**: Gateway with `backend="claude", transport="stdio"` config starts without error. A test confirming `ClaudeStdioAdapter.transport == "stdio"` passes.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final verification sweep and any remaining cleanup.

- [X] T015 [P] Grep `cli_bridge/` and `tests/` for remaining bare `driver.mode` attribute accesses (excluding the `@property mode` definition itself and `model_validator` migration code) and update any missed references to use `driver.backend` or `driver.transport`

- [X] T016 Run full test suite `uv run pytest` and fix any failures; run `uv run ruff check cli_bridge tests` and fix any lint issues

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — create stub immediately
- **Phase 2 (Foundational)**: No dependencies — can start immediately (T002 and T003 are parallel)
- **Phase 3 (US1)**: Depends on Phase 2 complete (T004, T005 need T002)
- **Phase 4 (US2)**: Depends on Phase 2 complete (T006–T011 need T002+T003); T009 depends on T006+T007+T008 complete
- **Phase 5 (US3)**: Depends on Phase 4 complete (T012 needs T009 done; T013 extends T009's adapter creation block)
- **Phase 6 (Polish)**: Depends on all phases complete

### Task-Level Dependencies

| Task | Depends On | Can Parallelize With |
|------|-----------|----------------------|
| T001 | — | T002, T003 |
| T002 | — | T001, T003 |
| T003 | — | T001, T002 |
| T004 | T002 | T005 |
| T005 | T002 | T004 |
| T006 | T002, T003 | T007, T008, T010, T011 |
| T007 | T002, T003 | T006, T008, T010, T011 |
| T008 | T002 | T006, T007, T010, T011 |
| T009 | T006, T007, T008 | T010, T011 |
| T010 | T003, T006, T007 | T009, T011 |
| T011 | T008 | T009, T010 |
| T012 | T001, T002, T003 | T013, T014 |
| T013 | T009, T012 | T014 |
| T014 | T012 | T013 |
| T015 | T002–T013 | T016 |
| T016 | T015 | — |

---

## Parallel Opportunities

### Phase 2 — run together:
```
T002: Update DriverConfig in schema.py
T003: Update BaseAdapter in base_adapter.py
```

### Phase 3 — run together after T002:
```
T004: Update loader.py config block builders
T005: Update test_migration.py
```

### Phase 4 — run together after T002+T003:
```
T006: Update IFlowAdapter in adapter.py
T007: Update ClaudeAdapter in claude_adapter.py
T008: Update check_backend_ready() in health.py
T010: Update inline_agents check in loop.py
T011: Update test_health_dispatch.py
```
Then, after T006+T007+T008 complete:
```
T009: Update commands.py (depends on T006, T007, T008)
```

### Phase 5 — T012+T013+T014:
```
T012: Implement ClaudeStdioAdapter (after T001,T002,T003)
T014: Write tests for ClaudeStdioAdapter (parallel with T012)
```
Then T013 after T009+T012 complete.

---

## Implementation Strategy

### MVP First (User Stories 1 + 2)

1. Complete Phase 1 + Phase 2 (T001–T003)
2. Complete Phase 3 (T004–T005) — verify backward compat
3. Complete Phase 4 (T006–T011) — verify dispatch
4. **STOP and VALIDATE**: run `uv run pytest`, confirm all existing tests pass
5. This delivers US1 + US2 as a complete, deployable increment

### Incremental Delivery

1. Setup + Foundational → schema migration works, `mode` property backward-compat
2. US1 (Phase 3) → old configs migrate silently
3. US2 (Phase 4) → dispatch uses new fields everywhere
4. **Deliverable**: all 4 existing backend/transport combinations work correctly
5. US3 (Phase 5) → claude+stdio newly enabled
6. Polish (Phase 6) → clean up remaining references

### Notes

- All T002 changes are in one file (`schema.py`) — be careful to keep the `mode` property and the legacy-detection validator logically separate
- T009 is the largest single-file change; consider reading all affected code blocks in `commands.py` before editing
- `ClaudeStdioAdapter` (T012) is the only truly new feature; model it closely on `StdioACPAdapter` in `cli_bridge/engine/stdio_acp.py`
- Always verify `uv run pytest` passes before moving between phases
