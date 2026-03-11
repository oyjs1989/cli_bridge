# Implementation Plan: Split Driver Config — Backend + Transport

**Branch**: `002-split-driver-config` | **Date**: 2026-03-11 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/002-split-driver-config/spec.md`

## Summary

Split `DriverConfig.mode` into two orthogonal fields: `backend` (iflow | claude) and `transport` (cli | stdio | acp). The legacy `mode` field is auto-migrated in-memory at startup — no disk rewrite. All four existing combinations continue to work unchanged. A fifth combination (claude + stdio) is newly enabled. The invalid combination (claude + acp) is rejected at validation time.

## Technical Context

**Language/Version**: Python 3.10+ (targets 3.10, 3.11, 3.12)
**Primary Dependencies**: Pydantic v2 (config/validation), Typer (CLI), loguru (logging), claude-agent-sdk (Claude backend)
**Storage**: JSON config file at `~/.cli-bridge/config.json`; session mappings at `~/.cli-bridge/session_mappings.json`
**Testing**: pytest with `asyncio_mode = "auto"` (all async test functions run automatically)
**Target Platform**: Linux/macOS/Windows (cross-platform)
**Project Type**: Gateway service / CLI tool
**Performance Goals**: No regression — same startup time and message-processing latency as before
**Constraints**: Zero breaking changes for any currently-supported `mode` value; in-memory migration only (no disk rewrite on startup)
**Scale/Scope**: ~7 source files modified; ~4 new test cases added; 1 new adapter file

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

No project-specific constitution has been ratified. Standard checks applied:

- [x] **No regressions**: All four existing mode combinations must continue working
- [x] **Testability**: Every new code path has corresponding test coverage
- [x] **Scope discipline**: claude+acp explicitly out of scope — one thing at a time
- [x] **Simplicity**: Migration via Pydantic `model_validator(mode="before")` — no separate migration service needed
- [x] **Backward compat**: `mode` property on `DriverConfig` derives from `backend`+`transport` → existing callers work unchanged

All gates pass. No violations to justify.

## Project Structure

### Documentation (this feature)

```text
specs/002-split-driver-config/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── config-schema-v3.md   # Phase 1 output
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (affected files)

```text
cli_bridge/
├── config/
│   ├── schema.py           # PRIMARY: split mode → backend + transport
│   └── loader.py           # Update driver block builders + default config writer
├── cli/
│   ├── commands.py         # Update mode references → backend/transport
│   └── health.py           # Update check_backend_ready() parameter
└── engine/
    ├── base_adapter.py     # Add inline_agents property
    ├── adapter.py          # Rename IFlowAdapter._mode → ._transport
    ├── claude_adapter.py   # Add transport property + support for stdio transport
    ├── claude_stdio_adapter.py   # NEW: persistent Claude process adapter
    └── loop.py             # Update inline_agents check

tests/
├── config/
│   └── test_migration.py   # Add new backend/transport tests; update existing
├── cli/
│   └── test_health_dispatch.py   # Update health check tests
└── engine/
    └── test_claude_stdio_adapter.py  # NEW: tests for claude+stdio
```

**Structure Decision**: Single project layout. No new packages needed — the new adapter is a peer to `claude_adapter.py` in `engine/`.

## Complexity Tracking

No constitution violations. No complexity to justify.
