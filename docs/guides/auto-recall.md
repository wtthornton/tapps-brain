# Auto-Recall: Pre-Prompt Memory Injection

Auto-recall automatically searches the memory store for relevant context before an agent processes a user message, and optionally captures new facts from agent responses.

## Overview

The recall loop has two phases:

1. **Recall** — user message arrives → search store → filter → format → inject into prompt
2. **Capture** — agent responds → extract durable facts → persist to store

```
User message
    │
    ▼
┌──────────────────┐
│ RecallOrchestrator│
│   .recall()      │──→ MemoryRetriever (BM25/FTS5/vector)
│                  │──→ Safety checks
│                  │──→ Token budget enforcement
│                  │──→ Scope/tier/branch filters
│                  │──→ Deduplication
└──────┬───────────┘
       │
       ▼
  RecallResult
  (memory_section, memories, token_count, recall_time_ms)
       │
       ▼
  Agent processes message with injected context
       │
       ▼
┌──────────────────┐
│ RecallOrchestrator│
│   .capture()     │──→ Rule-based extraction (extraction.py)
│                  │──→ Deduplication against store
└──────────────────┘
```

## Quick Start

### Simplest: 1 line via MemoryStore

```python
from pathlib import Path
from tapps_brain.store import MemoryStore

store = MemoryStore(Path("/my/project"))
result = store.recall("What database do we use?")
print(result.memory_section)   # Formatted markdown ready for injection
print(result.memory_count)     # Number of memories injected
```

### Recall + Capture (5 lines)

```python
from pathlib import Path
from tapps_brain.store import MemoryStore
from tapps_brain.recall import RecallOrchestrator

store = MemoryStore(Path("/my/project"))
orch = RecallOrchestrator(store)

# Before processing the user's message
context = orch.recall("How do we deploy?")
prompt = f"{context.memory_section}\n\nUser: How do we deploy?"

# After the agent responds
agent_response = "We decided to migrate from ECS to Kubernetes."
new_keys = orch.capture(agent_response)
```

### With Configuration

```python
from tapps_brain.models import MemoryScope, MemoryTier
from tapps_brain.recall import RecallConfig, RecallOrchestrator

config = RecallConfig(
    engagement_level="high",     # "high", "medium", or "low"
    max_tokens=3000,             # Token budget for injected context
    min_score=0.3,               # Minimum composite score threshold
    min_confidence=0.1,          # Minimum confidence threshold
    scope_filter=MemoryScope.project,  # Only project-scoped memories
    tier_filter=MemoryTier.architectural,  # Only architectural memories
    branch="feature-x",         # Only include this branch's memories
    dedupe_window=["key1"],     # Keys already in context (skip these)
)

orch = RecallOrchestrator(store, config=config)
result = orch.recall("What is our tech stack?")
```

## Configuration Reference

### RecallConfig

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `engagement_level` | `str` | `"high"` | Controls how many memories to inject. `"high"` = up to 5, `"medium"` = up to 3, `"low"` = none |
| `max_tokens` | `int` | `3000` | Maximum token budget for the injected memory section |
| `min_score` | `float` | `0.3` | Minimum composite retrieval score to include a memory |
| `min_confidence` | `float` | `0.1` | Minimum confidence (after decay) to include a memory |
| `scope_filter` | `MemoryScope \| None` | `None` | Only include memories with this scope |
| `tier_filter` | `MemoryTier \| None` | `None` | Only include memories with this tier |
| `branch` | `str \| None` | `None` | Only include branch-scoped memories for this branch |
| `dedupe_window` | `list[str]` | `[]` | Memory keys already in context — excluded from results |

### Engagement Levels

| Level | Behavior |
|-------|----------|
| `high` | Inject up to 5 memories, minimum score 0.3 |
| `medium` | Inject up to 3 memories, minimum confidence 0.5 |
| `low` | Never inject (returns empty result) |

### RecallResult

| Field | Type | Description |
|-------|------|-------------|
| `memory_section` | `str` | Formatted markdown section for prompt injection |
| `memories` | `list[dict]` | Metadata for each injected memory (key, confidence, tier, score, stale) |
| `token_count` | `int` | Estimated token count of the memory section |
| `recall_time_ms` | `float` | Wall-clock time for the recall operation |
| `truncated` | `bool` | Whether results were truncated due to token budget |
| `memory_count` | `int` | Number of memories in the result |

## Token Budget

The token budget (`max_tokens`, default 2000) limits how much context is injected. Memories are added in score order until the budget is exhausted. If a single memory exceeds the remaining budget and at least one memory has already been added, injection stops.

Token estimation uses a simple heuristic: 1 token ~ 4 characters.

## Protocol Interface

Host agents can implement the `RecallHookLike` and `CaptureHookLike` protocols for custom integration:

```python
from tapps_brain._protocols import RecallHookLike, CaptureHookLike
from tapps_brain.models import RecallResult

class MyRecallHook:
    def recall(self, message: str, **kwargs) -> RecallResult:
        # Custom recall logic
        ...

class MyCaptureHook:
    def capture(self, response: str, **kwargs) -> list[str]:
        # Custom capture logic
        ...

# Both are runtime-checkable:
assert isinstance(MyRecallHook(), RecallHookLike)
assert isinstance(MyCaptureHook(), CaptureHookLike)
```

The default `RecallOrchestrator` satisfies both protocols.

## Example: Claude Code Integration

```python
from pathlib import Path
from tapps_brain.store import MemoryStore
from tapps_brain.recall import RecallOrchestrator, RecallConfig

store = MemoryStore(Path.cwd())
orch = RecallOrchestrator(store, config=RecallConfig(max_tokens=1500))

def on_user_message(message: str) -> str:
    """Hook called before the agent processes a message."""
    result = orch.recall(message)
    if result.memory_section:
        return f"{result.memory_section}\n\n{message}"
    return message

def on_agent_response(response: str) -> None:
    """Hook called after the agent responds."""
    orch.capture(response)
```
