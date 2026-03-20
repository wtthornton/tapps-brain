# Ralph Fix Plan — tapps-brain

## High Priority (Critical/High epics)

### EPIC-008: MCP Server (Critical)
- [ ] Read EPIC-008.md and understand all stories
  - Done when: a short note is added to loop output confirming EPIC-008 stories were reviewed and mapped to code files.
- [ ] Implement MCP server bootstrap and server lifecycle wiring
  - Done when: `src/tapps_brain/mcp_server.py` exposes server initialization + lifecycle entrypoints and imports resolve cleanly.
- [ ] Add MCP tool handler: store
  - Done when: a callable handler for store exists in `src/tapps_brain/mcp_server.py` and has unit coverage in `tests/unit/test_mcp_server.py`.
- [ ] Add MCP tool handler: recall
  - Done when: a callable handler for recall exists in `src/tapps_brain/mcp_server.py` and has unit coverage in `tests/unit/test_mcp_server.py`.
- [ ] Add MCP tool handler: search
  - Done when: a callable handler for search exists in `src/tapps_brain/mcp_server.py` and has unit coverage in `tests/unit/test_mcp_server.py`.
- [ ] Add MCP tool handler: forget
  - Done when: a callable handler for forget exists in `src/tapps_brain/mcp_server.py` and has unit coverage in `tests/unit/test_mcp_server.py`.
- [ ] Add MCP resource endpoint: memory stats
  - Done when: a memory-stats resource endpoint exists in `src/tapps_brain/mcp_server.py` and has unit coverage in `tests/unit/test_mcp_server.py`.
- [ ] Add MCP resource endpoint: health
  - Done when: a health resource endpoint exists in `src/tapps_brain/mcp_server.py` and has unit coverage in `tests/unit/test_mcp_server.py`.
- [ ] Write targeted tests for MCP tool handlers
  - Done when: targeted MCP tool handler tests exist and pass in `tests/unit/test_mcp_server.py`.
- [ ] Write targeted tests for MCP resource endpoints
  - Done when: targeted MCP resource endpoint tests exist and pass in `tests/unit/test_mcp_server.py`.
- [ ] Run full MCP server integration tests
  - Done when: `pytest tests/unit/test_mcp_server.py -v` passes and results are captured in loop output.
- [ ] Update docs for MCP usage
  - Done when: MCP usage docs are present/updated in `docs/planning/epics/EPIC-008.md` (or a dedicated user-facing doc if added) with runnable examples.

### EPIC-006: Knowledge Graph (High)
- [ ] Read EPIC-006.md and understand all stories
  - Done when: loop output confirms EPIC-006 stories were reviewed and mapped to target modules/tests.
- [ ] Implement persistent knowledge graph with entity/relation storage
  - Done when: persistent entity/relation storage is implemented with deterministic behavior and covered by unit tests.
- [ ] Add semantic query support over the graph
  - Done when: graph query APIs support semantic retrieval use cases defined by EPIC-006 and have passing tests.
- [ ] Integrate graph with existing retrieval pipeline
  - Done when: retrieval pipeline wiring includes graph-backed signals without breaking existing retrieval tests.
- [ ] Write tests for graph operations
  - Done when: graph unit/integration tests are added and pass for create/read/update/query operations.

### EPIC-009: Multi-Interface Distribution (High)
- [ ] Read EPIC-009.md and understand all stories
  - Done when: loop output confirms EPIC-009 stories were reviewed and mapped to packaging/interface files.
- [ ] Package tapps-brain as library, CLI, and MCP server
  - Done when: install/build artifacts expose importable library APIs plus runnable CLI and MCP entrypoints.
- [ ] Ensure clean separation of interfaces
  - Done when: shared core logic is separated from interface adapters, with no interface-specific coupling in core modules.
- [ ] Add distribution/packaging configuration
  - Done when: packaging metadata/config supports distribution of selected interfaces and passes build checks.

## Medium Priority

### EPIC-007: Observability (Medium)
- [ ] Read EPIC-007.md and understand all stories
  - Done when: loop output confirms EPIC-007 stories were reviewed and mapped to observability modules/tests.
- [ ] Implement metrics collection and health checks
  - Done when: metrics + health-check functionality exists with deterministic outputs and passing tests.
- [ ] Add audit trail query capabilities
  - Done when: audit query APIs/commands exist and are validated by tests for filtering and retrieval behavior.
- [ ] Add monitoring endpoints/commands
  - Done when: monitoring endpoints/commands are implemented, documented, and covered by targeted tests.

## Completed
- [x] Project enabled for Ralph
- [x] EPIC-001: Test suite quality — A+ (done)
- [x] EPIC-002: Integration wiring (done)
- [x] EPIC-003: Auto-recall orchestrator (done)
- [x] EPIC-004: Bi-temporal fact versioning (done)
- [x] EPIC-005: CLI tool (done)

## Notes
- EPIC-008 (MCP) is top priority — treat MCP work as the main focus
- Always check the epic file in docs/planning/epics/ for detailed stories
- Maintain 95% test coverage
- Run full lint/type/test suite before committing
