"""Unit tests for tapps_brain.extraction (Epic 65.5)."""

from __future__ import annotations

from tapps_brain.extraction import extract_durable_facts


class TestExtractDurableFacts:
    """Tests for extract_durable_facts."""

    def test_empty_context_returns_empty(self) -> None:
        """Empty context returns empty list."""
        assert extract_durable_facts("") == []
        assert extract_durable_facts("   ") == []

    def test_no_decision_patterns_returns_empty(self) -> None:
        """Context without decision-like phrases returns empty."""
        ctx = "We ran the tests. All passed. The build completed."
        assert extract_durable_facts(ctx) == []

    def test_we_decided_extracts(self) -> None:
        """'we decided' phrase is extracted."""
        ctx = "We decided to use FastAPI for the API layer."
        facts = extract_durable_facts(ctx)
        assert len(facts) == 1
        assert facts[0]["key"]
        assert "FastAPI" in facts[0]["value"]
        assert facts[0]["tier"] == "architectural"

    def test_key_decision_extracts(self) -> None:
        """'key decision' phrase is extracted."""
        ctx = "A key decision was to store sessions in Redis."
        facts = extract_durable_facts(ctx)
        assert len(facts) >= 1
        assert any("Redis" in f["value"] for f in facts)

    def test_architecture_choice_extracts(self) -> None:
        """'architecture choice' phrase is extracted."""
        ctx = "The architecture choice was microservices for scaling."
        facts = extract_durable_facts(ctx)
        assert len(facts) >= 1
        assert any("microservices" in f["value"] for f in facts)

    def test_we_agreed_extracts(self) -> None:
        """'we agreed' phrase is extracted."""
        ctx = "We agreed on using ruff for linting."
        facts = extract_durable_facts(ctx)
        assert len(facts) >= 1
        assert any("ruff" in f["value"] for f in facts)

    def test_important_colon_extracts(self) -> None:
        """'important:' phrase is extracted."""
        ctx = "Important: Never commit API keys to the repo."
        facts = extract_durable_facts(ctx)
        assert len(facts) >= 1

    def test_max_facts_limit(self) -> None:
        """max_facts limits output."""
        ctx = "\n\n".join(
            f"We decided to use option {i} for component {i}."
            for i in range(15)
        )
        facts = extract_durable_facts(ctx, max_facts=5)
        assert len(facts) <= 5

    def test_max_value_chars_truncates(self) -> None:
        """max_value_chars truncates value with ellipsis."""
        long_val = "We decided to " + "x" * 5000 + " end."
        facts = extract_durable_facts(long_val, max_value_chars=100)
        assert facts
        assert len(facts[0]["value"]) <= 103  # 100 + "..."

    def test_deterministic_same_input_same_output(self) -> None:
        """Same input produces same output (deterministic)."""
        ctx = "We decided to use Pydantic for validation."
        a = extract_durable_facts(ctx)
        b = extract_durable_facts(ctx)
        assert a == b

    def test_returns_key_value_tier(self) -> None:
        """Returned dicts have key, value, tier."""
        ctx = "We decided to use PostgreSQL."
        facts = extract_durable_facts(ctx)
        assert facts
        for f in facts:
            assert "key" in f
            assert "value" in f
            assert "tier" in f
            assert f["tier"] in ("architectural", "pattern", "context")
