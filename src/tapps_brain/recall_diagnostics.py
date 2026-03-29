"""Machine-readable codes for empty recall / injection (agent observability).

Used in ``inject_memories`` return dict and :class:`~tapps_brain.models.RecallResult`.
"""

from __future__ import annotations

# Engagement / pipeline
RECALL_EMPTY_ENGAGEMENT_LOW = "engagement_low"
RECALL_EMPTY_SEARCH_FAILED = "search_failed"

# Store state
RECALL_EMPTY_STORE_EMPTY = "store_empty"
RECALL_EMPTY_GROUP_EMPTY = "group_empty"

# Retrieval
RECALL_EMPTY_NO_RANKED_MATCHES = "no_ranked_matches"
RECALL_EMPTY_BELOW_SCORE_THRESHOLD = "below_score_threshold"
RECALL_EMPTY_RAG_BLOCKED = "rag_safety_blocked"
RECALL_EMPTY_TOKEN_BUDGET = "token_budget_exhausted"

# Recall orchestrator post-step (not from inject_memories)
RECALL_EMPTY_POST_FILTER = "post_filter_excluded"
