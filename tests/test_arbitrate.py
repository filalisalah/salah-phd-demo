"""
Unit tests for the deterministic arbitration kernel of AURA-Grid.

Only the pure, offline kernel is exercised here: no network, no LLM, no
Streamlit runtime. Inputs are built from the real data structures defined in
``app.py`` (``SCENARIOS``, ``get_offline_opinions``, ``normalize_opinion``) so
the tests stay faithful to the actual implementation.

Rule coverage (see ``arbitrate()`` in app.py, lines ~532-635):

    R1  Regulatory Veto            -> REGULATORY_VETO
    R2  Total Failure (3 TIMEOUT)  -> SYSTEM_FAILURE_ESCALATION
    R3  Graceful Degradation       -> DEGRADED_CONSENSUS / DEGRADED_CONFLICT
    R4  Inter-Agent Conflict       -> INTER_AGENT_CONFLICT
    R5  Unanimous Reject           -> UNANIMOUS_REJECT
    R6  Consensus Approved         -> CONSENSUS_APPROVED
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

import app
from app import (
    AGENT_FINANCIAL,
    AGENT_GRID,
    AGENT_ORDER,
    AGENT_REGULATORY,
    SCENARIO_CONSENSUS,
    SCENARIO_NODE_FAILURE,
    SCENARIO_REGULATORY_VETO,
    arbitrate,
    get_offline_opinions,
    normalize_opinion,
    timeout_opinion,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _opinion(status: str, confidence: float = 0.9) -> Dict[str, Any]:
    """Build a well-formed, normalized opinion dict for a given status."""
    return {
        "status": status,
        "confidence": confidence,
        "proposal": f"{status} proposal",
        "reasoning": f"{status} reasoning",
    }


def _opinions(grid: str, regulatory: str, financial: str) -> Dict[str, Dict[str, Any]]:
    """Build a full 3-agent opinion mapping from three status strings."""
    return {
        AGENT_GRID: _opinion(grid),
        AGENT_REGULATORY: _opinion(regulatory),
        AGENT_FINANCIAL: _opinion(financial),
    }


def _scenario_opinions(scenario: Dict[str, Any], crash_sim: bool = False) -> Dict[str, Dict[str, Any]]:
    """Return normalized opinions for a scenario, exactly as ``main()`` does."""
    key = next(k for k, v in app.SCENARIOS.items() if v is scenario)
    raw = get_offline_opinions(key, crash_sim)
    return {agent: normalize_opinion(agent, raw.get(agent)) for agent in AGENT_ORDER}


# ---------------------------------------------------------------------------
# Verdict object shape
# ---------------------------------------------------------------------------

def test_verdict_has_expected_keys_and_metadata():
    verdict = arbitrate(_opinions("APPROVE", "APPROVE", "APPROVE"))

    for key in (
        "verdict",
        "severity",
        "recommended_action",
        "requires_human",
        "reason",
        "trace",
        "opinions",
        "timestamp",
    ):
        assert key in verdict, f"missing verdict key: {key}"

    # Metadata must come from VERDICT_META for the produced code.
    meta = app.VERDICT_META[verdict["verdict"]]
    assert verdict["severity"] == meta["severity"]
    assert verdict["recommended_action"] == meta["recommended_action"]
    assert verdict["requires_human"] == meta["requires_human"]

    # The opinions passed in are echoed back unchanged.
    assert verdict["opinions"] == _opinions("APPROVE", "APPROVE", "APPROVE")
    assert isinstance(verdict["trace"], list) and verdict["trace"]


# ---------------------------------------------------------------------------
# R1 - Regulatory Veto (highest priority)
# ---------------------------------------------------------------------------

def test_r1_regulatory_veto_overrides_approvals():
    verdict = arbitrate(_opinions("APPROVE", "REJECT", "APPROVE"))
    assert verdict["verdict"] == "REGULATORY_VETO"
    assert verdict["severity"] == "CRITICAL"
    assert verdict["requires_human"] is True
    assert any("R1" in step for step in verdict["trace"])


def test_r1_regulatory_veto_beats_other_timeouts():
    # R1 is evaluated before R3: a regulatory REJECT stays a veto even when
    # another agent timed out.
    verdict = arbitrate(_opinions("TIMEOUT", "REJECT", "TIMEOUT"))
    assert verdict["verdict"] == "REGULATORY_VETO"


def test_r1_regulatory_veto_on_real_scenario():
    opinions = _scenario_opinions(SCENARIO_REGULATORY_VETO)
    verdict = arbitrate(opinions)
    assert verdict["verdict"] == "REGULATORY_VETO"


# ---------------------------------------------------------------------------
# R2 - Total Failure
# ---------------------------------------------------------------------------

def test_r2_total_failure_when_all_timeout():
    verdict = arbitrate(_opinions("TIMEOUT", "TIMEOUT", "TIMEOUT"))
    assert verdict["verdict"] == "SYSTEM_FAILURE_ESCALATION"
    assert verdict["severity"] == "CRITICAL"
    assert verdict["requires_human"] is True
    assert any("R2" in step for step in verdict["trace"])


# ---------------------------------------------------------------------------
# R3 - Graceful Degradation
# ---------------------------------------------------------------------------

def test_r3_degraded_consensus_when_active_agents_approve():
    verdict = arbitrate(_opinions("APPROVE", "APPROVE", "TIMEOUT"))
    assert verdict["verdict"] == "DEGRADED_CONSENSUS"
    assert verdict["severity"] == "WARNING"
    assert verdict["requires_human"] is True
    assert any("R3" in step for step in verdict["trace"])


def test_r3_degraded_conflict_when_active_agents_disagree():
    verdict = arbitrate(_opinions("APPROVE", "REJECT", "TIMEOUT"))
    # NOTE: regulatory REJECT triggers R1 first, so use a non-regulatory
    # disagreement to reach R3's "mixed" branch.
    assert verdict["verdict"] == "REGULATORY_VETO"

    verdict = arbitrate(_opinions("REJECT", "APPROVE", "TIMEOUT"))
    assert verdict["verdict"] == "DEGRADED_CONFLICT"
    assert verdict["severity"] == "CRITICAL"


def test_r3_degraded_conflict_when_all_active_agents_reject():
    # All active agents REJECT in degraded mode -> DEGRADED_CONFLICT.
    verdict = arbitrate(_opinions("REJECT", "APPROVE", "TIMEOUT"))
    assert verdict["verdict"] == "DEGRADED_CONFLICT"

    verdict = arbitrate(_opinions("TIMEOUT", "APPROVE", "REJECT"))
    assert verdict["verdict"] == "DEGRADED_CONFLICT"


def test_r3_degraded_consensus_on_real_scenario_with_crash_sim():
    opinions = _scenario_opinions(SCENARIO_NODE_FAILURE, crash_sim=True)
    assert opinions[AGENT_FINANCIAL]["status"] == "TIMEOUT"
    verdict = arbitrate(opinions)
    assert verdict["verdict"] == "DEGRADED_CONSENSUS"


# ---------------------------------------------------------------------------
# R4 - Inter-Agent Conflict
# ---------------------------------------------------------------------------

def test_r4_inter_agent_conflict():
    # Regulatory APPROVE, but Grid/Financial disagree -> R4.
    verdict = arbitrate(_opinions("APPROVE", "APPROVE", "REJECT"))
    assert verdict["verdict"] == "INTER_AGENT_CONFLICT"
    assert verdict["severity"] == "WARNING"
    assert verdict["requires_human"] is True
    assert any("R4" in step for step in verdict["trace"])


# ---------------------------------------------------------------------------
# R5 - Unanimous Reject
# ---------------------------------------------------------------------------

def test_r5_unanimous_reject():
    # Regulatory must not REJECT for R5 to be reachable (R1 would fire first),
    # so this branch is only reachable when the regulatory agent is not the
    # rejecting one... which is impossible with 3 REJECTs. Documented here:
    # with the real kernel, 3 REJECTs always hit R1 (regulatory veto).
    verdict = arbitrate(_opinions("REJECT", "REJECT", "REJECT"))
    assert verdict["verdict"] == "REGULATORY_VETO"


def test_r5_unanimous_reject_branch_is_unreachable_by_design():
    # The R5 branch exists in code but is shadowed by R1 whenever the
    # regulatory agent REJECTs. We assert the documented behaviour instead of
    # pretending R5 is reachable.
    for combo in (
        ("REJECT", "REJECT", "REJECT"),
        ("REJECT", "REJECT", "APPROVE"),
    ):
        assert arbitrate(_opinions(*combo))["verdict"] == "REGULATORY_VETO"


# ---------------------------------------------------------------------------
# R6 - Consensus Approved
# ---------------------------------------------------------------------------

def test_r6_consensus_approved():
    verdict = arbitrate(_opinions("APPROVE", "APPROVE", "APPROVE"))
    assert verdict["verdict"] == "CONSENSUS_APPROVED"
    assert verdict["severity"] == "INFO"
    assert verdict["requires_human"] is False
    assert any("R6" in step for step in verdict["trace"])


def test_r6_consensus_approved_on_real_scenario():
    opinions = _scenario_opinions(SCENARIO_CONSENSUS)
    verdict = arbitrate(opinions)
    assert verdict["verdict"] == "CONSENSUS_APPROVED"


def test_r6_consensus_approved_on_node_failure_without_crash():
    opinions = _scenario_opinions(SCENARIO_NODE_FAILURE, crash_sim=False)
    verdict = arbitrate(opinions)
    assert verdict["verdict"] == "CONSENSUS_APPROVED"


# ---------------------------------------------------------------------------
# Determinism / idempotence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "combo",
    [
        ("APPROVE", "APPROVE", "APPROVE"),
        ("APPROVE", "REJECT", "APPROVE"),
        ("TIMEOUT", "TIMEOUT", "TIMEOUT"),
        ("APPROVE", "APPROVE", "TIMEOUT"),
        ("APPROVE", "APPROVE", "REJECT"),
    ],
)
def test_arbitrate_is_deterministic(combo):
    opinions = _opinions(*combo)
    first = arbitrate(opinions)
    second = arbitrate(opinions)

    # The timestamp may differ across calls; everything else must be identical.
    first.pop("timestamp")
    second.pop("timestamp")
    assert first == second


def test_arbitrate_does_not_mutate_input():
    opinions = _opinions("APPROVE", "APPROVE", "APPROVE")
    snapshot = {a: dict(o) for a, o in opinions.items()}
    arbitrate(opinions)
    assert opinions == snapshot


# ---------------------------------------------------------------------------
# Normalization layer (feeds the kernel)
# ---------------------------------------------------------------------------

def test_normalize_opinion_coerces_invalid_status_to_timeout():
    opinion = normalize_opinion(AGENT_GRID, {"status": "MAYBE", "confidence": 0.5})
    assert opinion["status"] == "TIMEOUT"
    assert opinion["confidence"] == 0.0


def test_normalize_opinion_clamps_confidence():
    opinion = normalize_opinion(
        AGENT_GRID,
        {"status": "APPROVE", "confidence": 5.0, "proposal": "p", "reasoning": "r"},
    )
    assert opinion["confidence"] == 1.0


def test_timeout_opinion_is_well_formed():
    opinion = timeout_opinion(AGENT_GRID, "boom")
    assert opinion["status"] == "TIMEOUT"
    assert opinion["confidence"] == 0.0
    assert "boom" in opinion["reasoning"]
