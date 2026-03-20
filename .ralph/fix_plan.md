# Ralph Fix Plan — tapps-brain

## High Priority (Critical/High epics)

### EPIC-008: MCP Server (Critical)
- [ ] Read EPIC-008.md and understand all stories
- [ ] Implement MCP server bootstrap and server lifecycle wiring
- [ ] Add MCP tool handler: store
- [ ] Add MCP tool handler: recall
- [ ] Add MCP tool handler: search
- [ ] Add MCP tool handler: forget
- [ ] Add MCP resource endpoint: memory stats
- [ ] Add MCP resource endpoint: health
- [ ] Write targeted tests for MCP tool handlers
- [ ] Write targeted tests for MCP resource endpoints
- [ ] Run full MCP server integration tests
- [ ] Update docs for MCP usage

### EPIC-006: Knowledge Graph (High)
- [ ] Read EPIC-006.md and understand all stories
- [ ] Implement persistent knowledge graph with entity/relation storage
- [ ] Add semantic query support over the graph
- [ ] Integrate graph with existing retrieval pipeline
- [ ] Write tests for graph operations

### EPIC-009: Multi-Interface Distribution (High)
- [ ] Read EPIC-009.md and understand all stories
- [ ] Package tapps-brain as library, CLI, and MCP server
- [ ] Ensure clean separation of interfaces
- [ ] Add distribution/packaging configuration

## Medium Priority

### EPIC-007: Observability (Medium)
- [ ] Read EPIC-007.md and understand all stories
- [ ] Implement metrics collection and health checks
- [ ] Add audit trail query capabilities
- [ ] Add monitoring endpoints/commands

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
