# Feature Specification: Split Driver Config — Backend + Transport

**Feature Branch**: `002-split-driver-config`
**Created**: 2026-03-11
**Status**: Draft
**Input**: User description: "Split DriverConfig.mode into separate backend and transport fields"

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Upgrade Without Config Changes (Priority: P1)

An operator running cli-bridge with an existing `~/.cli-bridge/config.json` upgrades to the new version. Their config still uses the old single `mode` field (e.g. `"mode": "claude"` or `"mode": "stdio"`). The gateway starts, auto-migrates their configuration silently, and behaves exactly as before — no manual edits required.

**Why this priority**: Backward compatibility is the highest risk item. If existing users' gateways break on upgrade, trust in the product is damaged. All other stories build on the system continuing to work.

**Independent Test**: Can be tested end-to-end by running the gateway with a legacy config file and confirming it starts correctly and processes messages as before.

**Acceptance Scenarios**:

1. **Given** a config with `driver.mode = "claude"`, **When** the gateway starts, **Then** the gateway runs as if `backend = "claude"` and `transport = "cli"` were set, with no errors or warnings about deprecated fields.
2. **Given** a config with `driver.mode = "stdio"`, **When** the gateway starts, **Then** the gateway runs as if `backend = "iflow"` and `transport = "stdio"` were set.
3. **Given** a config with `driver.mode = "cli"`, **When** the gateway starts, **Then** the gateway runs as if `backend = "iflow"` and `transport = "cli"` were set.
4. **Given** a config with `driver.mode = "acp"`, **When** the gateway starts, **Then** the gateway runs as if `backend = "iflow"` and `transport = "acp"` were set.
5. **Given** a config using the new `backend`/`transport` fields, **When** the gateway starts, **Then** it behaves correctly with no migration step needed.

---

### User Story 2 — Configure Backend and Transport Independently (Priority: P2)

An operator wants to configure which AI tool runs (iflow or claude) separately from how the gateway communicates with it (subprocess per message, long-running process, or WebSocket). They can set `driver.backend` and `driver.transport` in the config independently, with the gateway validating the combination is supported.

**Why this priority**: This is the core value of the feature — clean separation of concerns lets operators reason about and configure each dimension independently.

**Independent Test**: Can be tested by writing a config with each valid `backend`+`transport` combination and confirming the gateway selects the correct adapter and backend-specific settings.

**Acceptance Scenarios**:

1. **Given** `backend = "iflow"` and `transport = "cli"`, **When** the gateway processes a message, **Then** it spawns an iflow subprocess per message.
2. **Given** `backend = "iflow"` and `transport = "stdio"`, **When** the gateway processes a message, **Then** it uses a long-running iflow process over stdin/stdout.
3. **Given** `backend = "iflow"` and `transport = "acp"`, **When** the gateway processes a message, **Then** it connects to an iflow ACP WebSocket server.
4. **Given** `backend = "claude"` and `transport = "cli"`, **When** the gateway processes a message, **Then** it invokes the claude CLI as a subprocess per message.
5. **Given** an unsupported combination is configured, **When** the gateway starts, **Then** it reports a clear validation error identifying which combination is not supported.

---

### User Story 3 — Run Claude as a Long-Lived Process (Priority: P3)

An operator wants to use Claude Code as the AI backend but prefers a long-running stdio process over spawning a new subprocess for each message. They set `backend = "claude"` and `transport = "stdio"` and the gateway manages a persistent Claude Code process, reusing it across messages.

**Why this priority**: This is a new capability enabled by the refactor. It benefits operators with high message volume or who want session continuity at the process level. Lower priority than backward compat and core config separation.

**Independent Test**: Can be tested by configuring `backend = "claude"` and `transport = "stdio"`, sending multiple messages, and verifying that only one Claude process is spawned and reused.

**Acceptance Scenarios**:

1. **Given** `backend = "claude"` and `transport = "stdio"`, **When** the first message arrives, **Then** a single long-running Claude process is started and kept alive.
2. **Given** that long-running Claude process is active, **When** a subsequent message arrives, **Then** it is sent to the existing process rather than spawning a new one.
3. **Given** `backend = "claude"` and `transport = "stdio"`, **When** the gateway stops, **Then** the Claude process is gracefully terminated.

---

### Edge Cases

- What happens when both the legacy `mode` field and new `backend`/`transport` fields are present in the config? The new fields take precedence; the legacy `mode` field is ignored with a deprecation notice.
- What happens when `backend` is set but `transport` is not? The system applies a sensible default transport per backend (`cli` for both iflow and claude).
- What happens when the backend-specific config block (e.g. `iflow` or `claude`) doesn't match the configured `backend`? The system auto-populates default values for the correct backend and ignores the mismatched block.
- What happens with `backend = "claude"` and `transport = "acp"`? If not yet implemented, the gateway rejects this combination with a clear "not yet supported" error.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The configuration schema MUST accept two independent fields — `backend` and `transport` — in place of the single `mode` field.
- **FR-002**: The system MUST auto-migrate any config that uses the legacy `mode` field to the equivalent `backend` + `transport` values at startup, without requiring operator action.
- **FR-003**: The system MUST support all currently-implemented backend/transport combinations: iflow+cli, iflow+stdio, iflow+acp, claude+cli.
- **FR-004**: The system MUST support the new claude+stdio combination introduced by this refactor.
- **FR-005**: The system MUST reject unsupported backend/transport combinations at startup with a descriptive error message that names the invalid combination.
- **FR-006**: Backend-specific configuration blocks (`iflow` and `claude`) MUST be auto-populated based on the `backend` field, not the legacy `mode` field.
- **FR-007**: All dispatch logic (adapter selection, health checks, CLI commands) MUST use the new `backend` and `transport` fields, with no remaining references to the legacy `mode` field in routing decisions.
- **FR-008**: The `cli-bridge status` and `cli-bridge model` commands MUST reflect the new backend/transport fields correctly.
- **FR-009**: The system MUST continue passing all existing tests without modification to the tests themselves (tests may need updated fixture configs, but test logic must remain valid).

### Key Entities

- **DriverConfig**: The driver configuration object. Previously had a single `mode` field; now has separate `backend` (iflow | claude) and `transport` (cli | stdio | acp) fields, plus shared fields (timeout, max_turns, workspace) and backend-specific nested blocks (iflow, claude).
- **Backend**: Which AI tool processes the message. Values: `iflow` or `claude`. Determines which backend-specific config block is populated and which adapter class is used.
- **Transport**: How the gateway communicates with the AI backend. Values: `cli` (subprocess per message), `stdio` (long-running process over stdin/stdout), `acp` (WebSocket connection). Determines the communication strategy within the chosen adapter.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of operators with existing configs can upgrade without modifying their config files — zero breaking changes for any currently-supported mode value.
- **SC-002**: All 4 currently-implemented backend/transport combinations (iflow+cli, iflow+stdio, iflow+acp, claude+cli) continue to route and process messages correctly after the refactor.
- **SC-003**: The new claude+stdio combination successfully processes messages end-to-end using a single persistent process.
- **SC-004**: Config validation catches and reports unsupported combinations before the gateway attempts to start, preventing cryptic runtime errors.
- **SC-005**: All existing automated tests pass without changes to test logic (config fixtures may be updated to use new field names).
- **SC-006**: An operator can understand the available configuration options by reading the config schema alone — no implicit coupling between fields.

## Assumptions

- The `claude + acp` combination is out of scope for this feature; the system will reject it with a "not yet supported" error.
- Default transport for `backend = "iflow"` when no transport is specified is `stdio` (matching current default behaviour).
- Default transport for `backend = "claude"` when no transport is specified is `cli` (matching current behaviour).
- The migration of legacy configs is in-memory only at startup; the config file on disk is NOT automatically rewritten to use the new field names. Operators can manually update their config files at their own pace.
- The `IFlowAdapter` class rename or restructuring is out of scope; only the dispatch logic and config schema are changed.
