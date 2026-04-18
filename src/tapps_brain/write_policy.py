"""Pluggable write-path policy for MemoryStore (TAP-560 / STORY-SC04).

Two built-in policies:

* :class:`DeterministicWritePolicy` — always returns ADD, preserving the
  current deterministic behaviour (dedup and conflict detection remain
  active as a separate layer).  This is the default and the safest choice.
* :class:`LLMWritePolicy` — calls an :class:`~tapps_brain.evaluation.LLMJudge`
  to decide ADD / UPDATE / DELETE / NOOP for each incoming write.  Opt-in
  only; enabled with ``TAPPS_BRAIN_WRITE_POLICY=llm`` or via the profile
  ``write_policy.mode`` key.  Requires an LLM judge backend.

Design notes
------------
* Safety first: the store always runs ``check_content_safety()`` *before*
  calling ``decide()``.  The LLM never sees unsanitised content.
* Deterministic is the default: ``DeterministicWritePolicy`` is a zero-cost
  no-op that returns ADD unconditionally.  No extra latency on the happy path.
* Rate limiting: :class:`LLMWritePolicy` accepts a per-minute cap via
  ``rate_limit_per_minute``; writes that exceed the cap fall back to ADD so
  callers never observe a write being silently dropped.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from tapps_brain.evaluation import LLMJudge
    from tapps_brain.models import MemoryEntry


logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Decision types
# ---------------------------------------------------------------------------


class WriteDecision(StrEnum):
    """The action a :class:`WritePolicy` recommends for an incoming write.

    * ``ADD``    — persist the new entry (default).
    * ``UPDATE`` — overwrite *target_key* with the incoming value.
    * ``DELETE`` — remove *target_key* and drop the incoming entry.
    * ``NOOP``   — skip the write; the best matching candidate is returned.
    """

    ADD = "add"
    UPDATE = "update"
    DELETE = "delete"
    NOOP = "noop"


@dataclass
class WritePolicyResult:
    """The result returned by :meth:`WritePolicy.decide`.

    Attributes:
        decision:   The recommended action.
        target_key: For ``UPDATE`` and ``DELETE``, the key of the existing
                    entry that should be affected.  ``None`` for ADD/NOOP.
        reasoning:  Free-form explanation (useful for audit logs and tests).
    """

    decision: WriteDecision
    target_key: str | None = None
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Policy implementations
# ---------------------------------------------------------------------------


class DeterministicWritePolicy:
    """Default write policy — always recommends ADD.

    This is a zero-cost no-op: no similarity search, no LLM call.  The
    existing dedup (Bloom filter) and conflict detection layers in
    :class:`~tapps_brain.store.MemoryStore` handle de-duplication and
    contradiction marking independently.
    """

    def decide(
        self,
        key: str,
        value: str,
        candidates: list[MemoryEntry],
    ) -> WritePolicyResult:
        """Return ADD unconditionally."""
        return WritePolicyResult(
            decision=WriteDecision.ADD,
            reasoning="deterministic policy — always add",
        )


class LLMWritePolicy:
    """LLM-assisted write policy (opt-in, ADD/UPDATE/DELETE/NOOP state machine).

    Mirrors the mem0-style four-way decision, but implemented as a standalone
    policy object so it can be swapped in without touching the core write path.

    The LLM receives the incoming key/value and the top-*N* candidate entries
    (by recency). It returns a JSON payload with ``action`` and ``target_key``.
    On any error (LLM failure, malformed JSON, rate limit exceeded) the policy
    falls back to ADD so the write always completes.

    Args:
        judge:                :class:`~tapps_brain.evaluation.LLMJudge` backend
                              (``AnthropicJudge``, ``OpenAIJudge``, etc.).
        candidates_limit:     Maximum number of existing entries to include in
                              the LLM prompt.  Defaults to 5.
        rate_limit_per_minute: Hard cap on LLM calls per 60-second window.
                              Writes beyond this cap fall back to ADD.
    """

    def __init__(
        self,
        judge: LLMJudge,
        *,
        candidates_limit: int = 5,
        rate_limit_per_minute: int = 60,
    ) -> None:
        self._judge = judge
        self._candidates_limit = candidates_limit
        self._rate_limit_per_minute = rate_limit_per_minute
        # Simple sliding-window rate-limit state (thread-safe via GIL for CPython).
        self._call_timestamps: list[float] = []

    # ------------------------------------------------------------------
    # Rate limit helpers
    # ------------------------------------------------------------------

    def _check_rate_limit(self) -> bool:
        """Return True if a call is allowed, False if the cap is exceeded."""
        now = time.monotonic()
        cutoff = now - 60.0
        self._call_timestamps = [t for t in self._call_timestamps if t > cutoff]
        if len(self._call_timestamps) >= self._rate_limit_per_minute:
            return False
        self._call_timestamps.append(now)
        return True

    # ------------------------------------------------------------------
    # Core decision
    # ------------------------------------------------------------------

    def decide(
        self,
        key: str,
        value: str,
        candidates: list[MemoryEntry],
    ) -> WritePolicyResult:
        """Consult the LLM to decide ADD / UPDATE / DELETE / NOOP.

        Falls back to ADD on rate-limit exceeded or any LLM/parsing error.
        """
        if not self._check_rate_limit():
            logger.warning(
                "write_policy.llm.rate_limit_exceeded",
                key=key,
                rate_limit=self._rate_limit_per_minute,
            )
            return WritePolicyResult(
                decision=WriteDecision.ADD,
                reasoning="rate limit exceeded — fallback to ADD",
            )

        top_candidates = candidates[: self._candidates_limit]
        try:
            return self._call_llm(key, value, top_candidates)
        except Exception:
            logger.warning(
                "write_policy.llm.error",
                key=key,
                exc_info=True,
            )
            return WritePolicyResult(
                decision=WriteDecision.ADD,
                reasoning="llm error — fallback to ADD",
            )

    def _build_prompt(
        self,
        key: str,
        value: str,
        candidates: list[MemoryEntry],
    ) -> str:
        """Build the LLM prompt for a write-path decision."""
        cand_lines = "\n".join(
            f"  [{i+1}] key={c.key!r} value={c.value!r}"
            for i, c in enumerate(candidates)
        )
        if not cand_lines:
            cand_lines = "  (none)"
        header = (
            "You are a memory-management assistant. "
            "Decide the best action for a new memory entry."
        )
        return (
            f"{header}\n\n"
            f"New entry:\n  key={key!r}\n  value={value!r}\n\n"
            f"Existing similar entries:\n{cand_lines}\n\n"
            "Choose one action:\n"
            '  ADD    — the new entry contains information not already present.\n'
            '  UPDATE — the new entry replaces an existing one (set target_key).\n'
            '  DELETE — an existing entry is wrong; remove it (set target_key).\n'
            '  NOOP   — the new entry is already captured; skip the write.\n\n'
            "Respond with JSON only (no markdown fences):\n"
            '{"action": "ADD|UPDATE|DELETE|NOOP", "target_key": null_or_string, '
            '"reasoning": "brief explanation"}'
        )

    def _call_llm(
        self,
        key: str,
        value: str,
        candidates: list[MemoryEntry],
    ) -> WritePolicyResult:
        """Call the judge and parse the response into a :class:`WritePolicyResult`."""
        import json

        prompt = self._build_prompt(key, value, candidates)
        # Reuse judge_relevance with the full prompt as the query.
        # The memory_value arg is irrelevant here; we use the prompt only.
        result = self._judge.judge_relevance(query=prompt, memory_value="")
        # The LLM embeds JSON in the reasoning field when called this way.
        # We try to parse the judge's reasoning as our decision payload.
        raw_text = result.reasoning or ""
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start >= 0 and end > start:
            raw_text = raw_text[start : end + 1]
        try:
            data: dict[str, Any] = json.loads(raw_text)
        except (json.JSONDecodeError, ValueError):
            # Judge returned its payload as the raw score — try the whole text.
            logger.debug("write_policy.llm.parse_fallback", key=key)
            return WritePolicyResult(
                decision=WriteDecision.ADD,
                reasoning="json parse failed — fallback to ADD",
            )

        action = str(data.get("action", "ADD")).upper()
        target_key: str | None = data.get("target_key") or None
        reasoning = str(data.get("reasoning", ""))

        try:
            decision = WriteDecision(action.lower())
        except ValueError:
            decision = WriteDecision.ADD
            reasoning = f"unknown action {action!r} — fallback to ADD"

        logger.info(
            "write_policy.llm.decision",
            key=key,
            decision=decision.value,
            target_key=target_key,
            reasoning=reasoning,
        )
        return WritePolicyResult(
            decision=decision,
            target_key=target_key,
            reasoning=reasoning,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_write_policy(
    mode: str,
    judge: LLMJudge | None = None,
    *,
    candidates_limit: int = 5,
    rate_limit_per_minute: int = 60,
) -> DeterministicWritePolicy | LLMWritePolicy:
    """Factory that returns the appropriate policy for *mode*.

    Args:
        mode:                 ``"deterministic"`` (default) or ``"llm"``.
        judge:                Required when *mode* is ``"llm"``.
        candidates_limit:     Forwarded to :class:`LLMWritePolicy`.
        rate_limit_per_minute: Forwarded to :class:`LLMWritePolicy`.

    Raises:
        ValueError: If *mode* is ``"llm"`` but *judge* is ``None``.
        ValueError: If *mode* is unrecognised.
    """
    mode = mode.strip().lower()
    if mode == "deterministic":
        return DeterministicWritePolicy()
    if mode == "llm":
        if judge is None:
            msg = (
                "TAPPS_BRAIN_WRITE_POLICY=llm requires an LLMJudge backend. "
                "Pass a judge instance or configure ANTHROPIC_API_KEY / OPENAI_API_KEY."
            )
            raise ValueError(msg)
        return LLMWritePolicy(
            judge,
            candidates_limit=candidates_limit,
            rate_limit_per_minute=rate_limit_per_minute,
        )
    msg = f"Unknown write policy mode {mode!r}. Valid values: 'deterministic', 'llm'."
    raise ValueError(msg)
