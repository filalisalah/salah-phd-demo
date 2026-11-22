"""
AURA-Grid Research Prototype & Architectural Demonstrator
=========================================================

Auditable & Reliable Multi-Agent System for Regulated Energy Operations.

This single-file Streamlit application demonstrates a three-layer architecture:

    Layer 1 - Bounded Multi-Agent Swarm
        Three domain-bounded agents (Grid Dispatch, Regulatory Compliance,
        Financial Risk) produce structured JSON opinions. They never decide;
        they only *propose*.

    Layer 2 - Deterministic Governance & Arbitration Kernel
        A pure-Python, rule-based engine (no LLM, no randomness) turns the
        opinions into a single, traceable verdict. It is the only component
        allowed to "decide".

    Layer 3 - Glass-Box Human-in-the-Loop Dashboard
        A Streamlit UI that exposes the full reasoning, lets an operator
        contest/overrule the decision, and appends every decision to an
        immutable, session-scoped audit log.

Design invariant: LLM agents are unreliable by nature. System reliability
comes from the deterministic kernel and human supervision, not from the model.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import base64
import functools
import json
import re
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import streamlit as st
import streamlit.components.v1 as components

# NOTE: `openai` is imported lazily inside the live LLM function so that the
# offline mode (Mode A) works even if the SDK is not installed.


# =============================================================================
# 1. GLOBAL CONSTANTS
# =============================================================================

# --- Intellectual property notice (displayed in header, footer and sidebar) --
IP_NOTICE = (
    "© 2026 EL FILALI SALAH ADDINE — Proprietary Academic Research Prototype. "
    "Authorized exclusively for University of Luxembourg (SnT / UBIX) evaluation "
    "and doctoral supervision upon admission (Ref: UOL08435). Non-production "
    "architectural demonstrator."
)

# --- Canonical agent identifiers -------------------------------------------
AGENT_GRID = "Grid_Dispatch_Agent"
AGENT_REGULATORY = "Regulatory_Compliance_Agent"
AGENT_FINANCIAL = "Financial_Risk_Agent"
AGENT_ORDER = [AGENT_GRID, AGENT_REGULATORY, AGENT_FINANCIAL]

# --- Opinion schema ---------------------------------------------------------
VALID_STATUSES = {"APPROVE", "REJECT", "TIMEOUT"}

# --- Verdict enumeration ----------------------------------------------------
VERDICTS = {
    "CONSENSUS_APPROVED",         # 3 APPROVE
    "UNANIMOUS_REJECT",           # 3 REJECT
    "REGULATORY_VETO",            # Regulatory REJECT (overrides everything)
    "INTER_AGENT_CONFLICT",       # APPROVE/REJECT disagreement -> human escalation
    "DEGRADED_CONSENSUS",         # >=1 TIMEOUT, active agents unanimously APPROVE
    "DEGRADED_CONFLICT",          # >=1 TIMEOUT, active agents disagree
    "SYSTEM_FAILURE_ESCALATION",  # 3 TIMEOUT
}

# --- Verdict metadata: severity, recommended action, human escalation -------
VERDICT_META: Dict[str, Dict[str, Any]] = {
    "CONSENSUS_APPROVED": {
        "severity": "INFO",
        "recommended_action": "Execute the proposed action.",
        "requires_human": False,
    },
    "UNANIMOUS_REJECT": {
        "severity": "WARNING",
        "recommended_action": "Do not execute; review the plan.",
        "requires_human": True,
    },
    "REGULATORY_VETO": {
        "severity": "CRITICAL",
        "recommended_action": "Block the action; an alternative is required.",
        "requires_human": True,
    },
    "INTER_AGENT_CONFLICT": {
        "severity": "WARNING",
        "recommended_action": "Mandatory human arbitration.",
        "requires_human": True,
    },
    "DEGRADED_CONSENSUS": {
        "severity": "WARNING",
        "recommended_action": "Execute with enhanced monitoring.",
        "requires_human": True,
    },
    "DEGRADED_CONFLICT": {
        "severity": "CRITICAL",
        "recommended_action": "Mandatory human arbitration.",
        "requires_human": True,
    },
    "SYSTEM_FAILURE_ESCALATION": {
        "severity": "CRITICAL",
        "recommended_action": "Switch to manual operation.",
        "requires_human": True,
    },
}


# =============================================================================
# 2. PRE-COMPUTED SCENARIOS (Mode A - offline)
# =============================================================================

SCENARIO_CONSENSUS = {
    "id": "SCENARIO_CONSENSUS",
    "context": "Minor substation load rebalancing, no regulatory or financial stakes.",
    "supports_crash_sim": False,
    "agents": {
        AGENT_GRID: {
            "status": "APPROVE",
            "confidence": 0.94,
            "proposal": (
                "Shift 12 MW of load from Substation B to Substation C via "
                "tie-breaker CB-204; target line loading 71% of thermal rating."
            ),
            "reasoning": (
                "Post-rebalance line loading stays below 80% of thermal rating "
                "on all feeders. N-1 contingency analysis shows no violation. "
                "Action is reversible within 90 seconds."
            ),
        },
        AGENT_REGULATORY: {
            "status": "APPROVE",
            "confidence": 0.91,
            "proposal": (
                "Authorize the rebalancing as proposed; all operating parameters "
                "remain within EN 50160 voltage and frequency envelopes."
            ),
            "reasoning": (
                "Projected voltage deviation is 1.8%, within the 10% statutory "
                "band. No grid-code exemption required. Switching sequence "
                "complies with local operational safety procedures."
            ),
        },
        AGENT_FINANCIAL: {
            "status": "APPROVE",
            "confidence": 0.88,
            "proposal": (
                "Proceed; estimated cost of 340 EUR for switching operations is "
                "offset by avoided imbalance penalties of approximately 1,200 EUR."
            ),
            "reasoning": (
                "Net positive expected value. No SLA exposure introduced. "
                "Financial exposure remains well below the 50k EUR operational "
                "risk threshold."
            ),
        },
    },
}

SCENARIO_REGULATORY_VETO = {
    "id": "SCENARIO_REGULATORY_VETO",
    "context": (
        "Peak overload at Kirchberg substation; emergency 115% line overrating "
        "proposed."
    ),
    "supports_crash_sim": False,
    "agents": {
        AGENT_GRID: {
            "status": "APPROVE",
            "confidence": 0.86,
            "proposal": (
                "Apply emergency line overrating on Kirchberg feeder K-07 up to "
                "115% of nominal thermal rating for 25 minutes to absorb the "
                "evening peak and prevent load shedding."
            ),
            "reasoning": (
                "Peak demand exceeds forecast by 9%. Without intervention, 40 MW "
                "of load shedding is likely. Short-duration overrating at 115% is "
                "within the conductor short-time emergency curve and avoids "
                "cascading outages."
            ),
        },
        AGENT_REGULATORY: {
            "status": "REJECT",
            "confidence": 0.97,
            "proposal": (
                "VETO the 115% overrating. Enforce a hard cap at 100% of nominal "
                "thermal rating and require an alternative remedial action."
            ),
            "reasoning": (
                "Sustained operation above 100% of nominal thermal rating violates "
                "the applicable grid-code safety margin for this asset class. The "
                "proposed 115% exceeds the maximum admissible emergency overrating "
                "of 105% and creates unacceptable conductor-sag and fire-risk "
                "exposure. Safety limits are non-negotiable."
            ),
        },
        AGENT_FINANCIAL: {
            "status": "APPROVE",
            "confidence": 0.79,
            "proposal": (
                "Approve the emergency overrating; the estimated 180k EUR SLA "
                "penalty from load shedding outweighs the marginal wear cost of "
                "short-duration overrating."
            ),
            "reasoning": (
                "Avoided SLA penalties and reputational cost dominate the expected "
                "maintenance impact. Financial exposure of inaction is materially "
                "higher than the cost of the proposed action."
            ),
        },
    },
}

SCENARIO_NODE_FAILURE = {
    "id": "SCENARIO_NODE_FAILURE",
    "context": (
        "Node N-12 failure; rerouting with optional Financial Agent crash "
        "simulation."
    ),
    "supports_crash_sim": True,
    "agents": {
        AGENT_GRID: {
            "status": "APPROVE",
            "confidence": 0.90,
            "proposal": (
                "Reroute 18 MW from the failed node N-12 to adjacent feeders F-03 "
                "and F-05; keep all remaining lines below 85% thermal rating."
            ),
            "reasoning": (
                "Node N-12 is unreachable. Topology reconfiguration restores supply "
                "to 96% of affected customers. N-1 analysis on the reconfigured "
                "network shows no violations."
            ),
        },
        AGENT_REGULATORY: {
            "status": "APPROVE",
            "confidence": 0.89,
            "proposal": (
                "Authorize the rerouting; all post-contingency parameters remain "
                "within statutory safety envelopes."
            ),
            "reasoning": (
                "The remedial topology keeps voltage and frequency within "
                "grid-code limits. No exemption is required for the rerouting "
                "action."
            ),
        },
        AGENT_FINANCIAL: {
            "status": "APPROVE",
            "confidence": 0.83,
            "proposal": (
                "Proceed with the rerouting; incremental cost of 2,100 EUR is "
                "acceptable versus an estimated 60k EUR outage impact."
            ),
            "reasoning": (
                "Restoring supply quickly minimizes regulatory outage-duration "
                "penalties. Net financial exposure is favorable."
            ),
        },
    },
    # Payload used when the "Simulate Financial Agent Timeout/Crash" checkbox
    # is enabled.
    "crash_override": {
        AGENT_FINANCIAL: {
            "status": "TIMEOUT",
            "confidence": 0.0,
            "proposal": "No proposal available.",
            "reasoning": (
                "Agent did not respond within the 5-second arbitration window "
                "(simulated node failure). Financial impact assessment unavailable."
            ),
        }
    },
}

# Ordered mapping used by the scenario selector.
SCENARIOS: Dict[str, Dict[str, Any]] = {
    "1 - Consensus Load Rebalancing": SCENARIO_CONSENSUS,
    "2 - Regulatory Veto Conflict (Kirchberg Peak)": SCENARIO_REGULATORY_VETO,
    "3 - Graceful Degradation (Node Failure)": SCENARIO_NODE_FAILURE,
}


# =============================================================================
# 3. BOUNDED SYSTEM PROMPTS (Mode B - live LLM)
# =============================================================================

SYSTEM_PROMPTS: Dict[str, str] = {
    AGENT_GRID: (
        "You are Grid_Dispatch_Agent, a bounded expert in electrical grid "
        "topology, power flow, thermal margins and dispatch actions.\n"
        "You must ONLY assess grid operations and dispatch feasibility. Do not "
        "discuss regulatory compliance or financial costs.\n"
        "Respond with a single JSON object and nothing else, using exactly these "
        "keys:\n"
        '{"status": "APPROVE" | "REJECT" | "TIMEOUT", "confidence": <float '
        '0.0-1.0>, "proposal": "<string>", "reasoning": "<string>"}\n'
        "Do not output any text outside the JSON object."
    ),
    AGENT_REGULATORY: (
        "You are Regulatory_Compliance_Agent, a bounded expert in energy grid "
        "regulatory compliance and safety limits.\n"
        "You must ONLY assess regulatory and safety compliance. Do not discuss "
        "costs or dispatch optimization.\n"
        "Respond with a single JSON object and nothing else, using exactly these "
        "keys:\n"
        '{"status": "APPROVE" | "REJECT" | "TIMEOUT", "confidence": <float '
        '0.0-1.0>, "proposal": "<string>", "reasoning": "<string>"}\n'
        'If any safety or grid-code limit would be violated, you MUST return '
        'status "REJECT".\n'
        "Do not output any text outside the JSON object."
    ),
    AGENT_FINANCIAL: (
        "You are Financial_Risk_Agent, a bounded expert in operational costs, "
        "SLA penalties, financial exposure and ROI of grid actions.\n"
        "You must ONLY assess financial and economic risk. Do not discuss "
        "electrical safety or regulatory compliance.\n"
        "Respond with a single JSON object and nothing else, using exactly these "
        "keys:\n"
        '{"status": "APPROVE" | "REJECT" | "TIMEOUT", "confidence": <float '
        '0.0-1.0>, "proposal": "<string>", "reasoning": "<string>"}\n'
        "Do not output any text outside the JSON object."
    ),
}


# =============================================================================
# 3b. CLOUDFLARE WEB ANALYTICS (privacy-first, cookieless)
# =============================================================================

# Cloudflare Web Analytics beacon.
#   Token   : 4350840e922b4933b8a6647a3af815bf
#   Source  : Cloudflare dashboard > Analytics & Logs > Web Analytics >
#             salah-phd-demo.streamlit.app > "Manage site" > JS snippet.
#   Hostname: salah-phd-demo.streamlit.app (Streamlit Community Cloud).
#
# Why this approach on Streamlit Community Cloud:
#   * Streamlit Cloud does not expose the served HTML <head>, so the snippet
#     cannot be pasted into the page template.
#   * `st.markdown(..., unsafe_allow_html=True)` does not reliably execute
#     <script> tags, so the beacon module was never started and Cloudflare
#     recorded zero visits.
#   * `st.components.v1.html()` renders inside an iframe. If the beacon were
#     loaded *inside* that iframe it would report `about:srcdoc` instead of the
#     real app URL. Instead, a tiny bootstrap runs in the (same-origin) iframe
#     and appends the real beacon <script> to `window.parent.document.head`, so
#     the beacon executes in the top-level Streamlit document and reports the
#     real URL/hostname.
#   * The injector is guarded by a DOM id (`cf-web-analytics`) so reruns do not
#     add duplicate beacons.
#   * A session-state flag avoids emitting the iframe on every rerun of the
#     same session (the DOM guard already makes it idempotent).
#   * Cloudflare can only attribute visits to the hostname configured for the
#     Web Analytics site; traffic served under a different hostname is not
#     counted for that site's token.
CLOUDFLARE_ANALYTICS_TOKEN = "4350840e922b4933b8a6647a3af815bf"

CLOUDFLARE_ANALYTICS_INJECTOR = (
    "<script>(function () {"
    "try {"
    "var parentDoc = window.parent.document;"
    "if (parentDoc.getElementById('cf-web-analytics')) { return; }"
    "var s = parentDoc.createElement('script');"
    "s.id = 'cf-web-analytics';"
    "s.type = 'module';"
    "s.src = 'https://static.cloudflareinsights.com/beacon.min.js';"
    "s.setAttribute('data-cf-beacon', JSON.stringify({token: '"
    + CLOUDFLARE_ANALYTICS_TOKEN
    + "', spa: true}));"
    "(parentDoc.head || parentDoc.documentElement).appendChild(s);"
    "} catch (err) {"
    "console.warn('Cloudflare Web Analytics injection failed:', err);"
    "}"
    "})();</script>"
)


def _inject_cloudflare_analytics() -> None:
    """
    Inject the Cloudflare Web Analytics beacon exactly once per session.

    Must be called after `st.set_page_config()` (Streamlit requirement) and
    before any other rendered element so the beacon loads as early as possible.
    The iframe (same-origin: `allow-same-origin` + `allow-scripts` in
    Streamlit's sandbox) appends the beacon to the parent document's <head>, so
    the beacon runs in the real page rather than in `about:srcdoc`. A
    session-state flag prevents emitting the iframe on every rerun, and a DOM
    id guard prevents duplicate beacon tags.
    """
    if st.session_state.get("_cf_analytics_injected"):
        return
    st.session_state["_cf_analytics_injected"] = True
    components.html(CLOUDFLARE_ANALYTICS_INJECTOR, height=0)


# =============================================================================
# 4. UTILITY HELPERS
# =============================================================================

def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (e.g. 2026-11-16T00:00:00Z)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def timeout_opinion(agent_name: str, reason: str) -> Dict[str, Any]:
    """Build a well-formed TIMEOUT opinion used for failures / malformed output."""
    return {
        "status": "TIMEOUT",
        "confidence": 0.0,
        "proposal": "No proposal available.",
        "reasoning": f"[{agent_name}] {reason}",
    }


def normalize_opinion(agent_name: str, raw: Any) -> Dict[str, Any]:
    """
    Validate and normalize a raw agent opinion (SPEC §4.1).

    Any missing key, wrong type, out-of-enum status or out-of-range confidence
    is coerced into a TIMEOUT opinion. The system never crashes on malformed
    agent output.
    """
    if not isinstance(raw, dict):
        return timeout_opinion(agent_name, "Malformed agent output (not a dict).")

    status = raw.get("status")
    if status not in VALID_STATUSES:
        return timeout_opinion(agent_name, f"Invalid status: {status!r}.")

    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))  # clamp to [0, 1]

    proposal = str(raw.get("proposal") or "").strip() or "No proposal provided."
    reasoning = str(raw.get("reasoning") or "").strip() or "No reasoning provided."

    return {
        "status": status,
        "confidence": confidence,
        "proposal": proposal,
        "reasoning": reasoning,
    }


# =============================================================================
# 5. AGENT FUNCTIONS
# =============================================================================

def get_offline_opinions(scenario_key: str, crash_sim: bool) -> Dict[str, Dict[str, Any]]:
    """
    Return the pre-computed opinions for a scenario (Mode A).

    If `crash_sim` is True and the scenario supports it, the Financial agent
    payload is replaced by the scenario's `crash_override` (TIMEOUT).
    """
    scenario = SCENARIOS[scenario_key]
    # Deep-copy the base opinions so callers cannot mutate the constants.
    opinions: Dict[str, Dict[str, Any]] = {
        agent: dict(opinion) for agent, opinion in scenario["agents"].items()
    }

    if crash_sim and scenario.get("supports_crash_sim"):
        override = scenario.get("crash_override", {})
        for agent, payload in override.items():
            opinions[agent] = dict(payload)

    return opinions


def parse_agent_json(agent_name: str, content: Optional[str]) -> Dict[str, Any]:
    """
    Robustly parse an LLM response into a normalized opinion (SPEC §5.2.2).

    Handles markdown code fences and extracts the first JSON object if the raw
    text is not directly parseable. Falls back to a TIMEOUT opinion on failure.
    """
    if not content:
        return timeout_opinion(agent_name, "Empty LLM response.")

    text = content.strip()
    # Strip optional markdown fences: ```json ... ``` or ``` ... ```
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Attempt to extract the first {...} object.
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return timeout_opinion(agent_name, "Unparseable LLM response.")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return timeout_opinion(agent_name, "Unparseable LLM response.")

    return normalize_opinion(agent_name, data)


def call_agent_llm(
    agent_name: str,
    system_prompt: str,
    user_prompt: str,
    api_key: str,
    timeout: int = 20,
) -> Dict[str, Any]:
    """
    Call DeepSeek via the OpenAI-compatible SDK and parse the response.

    Any failure (missing SDK, network error, invalid key, unparseable output)
    is converted into a TIMEOUT opinion so the kernel can degrade gracefully.
    """
    try:
        # Lazy import: keeps Mode A functional even without the SDK installed.
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
            timeout=timeout,
        )
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        return parse_agent_json(agent_name, content)
    except Exception as exc:  # noqa: BLE001 - deliberate broad catch for resilience
        return timeout_opinion(agent_name, f"LLM call failed: {exc}")


def get_live_opinions(scenario_key: str, api_key: str) -> Dict[str, Dict[str, Any]]:
    """
    Query each agent live via DeepSeek (Mode B).

    Each agent receives its bounded system prompt and a user prompt containing
    the scenario context. Failures degrade to TIMEOUT per agent.
    """
    scenario = SCENARIOS[scenario_key]
    context = scenario["context"]

    opinions: Dict[str, Dict[str, Any]] = {}
    for agent_name in AGENT_ORDER:
        user_prompt = (
            f"Scenario context: {context}\n\n"
            "Assess the following operational situation strictly within your "
            "bounded domain and return your structured opinion as JSON."
        )
        opinions[agent_name] = call_agent_llm(
            agent_name=agent_name,
            system_prompt=SYSTEM_PROMPTS[agent_name],
            user_prompt=user_prompt,
            api_key=api_key,
        )
    return opinions


# =============================================================================
# 6. DETERMINISTIC ARBITRATION KERNEL (Layer 2)
# =============================================================================

def build_verdict(
    code: str,
    opinions: Dict[str, Dict[str, Any]],
    trace: List[str],
    reason: str,
) -> Dict[str, Any]:
    """Assemble a traceable verdict object from a verdict code and metadata."""
    meta = VERDICT_META[code]
    return {
        "verdict": code,
        "severity": meta["severity"],
        "recommended_action": meta["recommended_action"],
        "requires_human": meta["requires_human"],
        "reason": reason,
        "trace": trace,
        "opinions": opinions,
        "timestamp": utc_now_iso(),
    }


def arbitrate(opinions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Pure, deterministic arbitration kernel (SPEC §4.2 / §4.4).

    Rules are evaluated strictly top-to-bottom (R1 -> R6). No LLM, no network,
    no randomness: identical inputs always yield the identical verdict.
    """
    trace: List[str] = []

    reg = opinions[AGENT_REGULATORY]
    active = {a: o for a, o in opinions.items() if o["status"] != "TIMEOUT"}
    timed_out = [a for a, o in opinions.items() if o["status"] == "TIMEOUT"]

    # R1 - Regulatory Veto (highest priority, overrides everything)
    if reg["status"] == "REJECT":
        trace.append("R1 Regulatory Veto triggered")
        return build_verdict(
            "REGULATORY_VETO",
            opinions,
            trace,
            reason=(
                "Regulatory_Compliance_Agent issued a REJECT; veto overrides all "
                "other opinions."
            ),
        )

    # R2 - Total Failure (all agents timed out)
    if len(active) == 0:
        trace.append("R2 Total agent failure")
        return build_verdict(
            "SYSTEM_FAILURE_ESCALATION",
            opinions,
            trace,
            reason="All agents timed out; manual operation required.",
        )

    active_statuses = {o["status"] for o in active.values()}
    unanimous_approve = active_statuses == {"APPROVE"}
    unanimous_reject = active_statuses == {"REJECT"}
    mixed = ("APPROVE" in active_statuses) and ("REJECT" in active_statuses)

    # R3 - Graceful Degradation (>=1 TIMEOUT but not all)
    if timed_out:
        trace.append(
            f"R3 Graceful Degradation: {len(timed_out)} agent(s) unavailable"
        )
        if unanimous_approve:
            return build_verdict(
                "DEGRADED_CONSENSUS",
                opinions,
                trace,
                reason=(
                    "Consensus reached on active agents; degraded mode with "
                    "monitoring."
                ),
            )
        if mixed:
            return build_verdict(
                "DEGRADED_CONFLICT",
                opinions,
                trace,
                reason=(
                    "Active agents disagree while one or more agents are "
                    "unavailable."
                ),
            )
        # All active agents REJECT
        return build_verdict(
            "DEGRADED_CONFLICT",
            opinions,
            trace,
            reason="Active agents reject the proposal in degraded mode.",
        )

    # R4 - Inter-Agent Conflict
    if mixed:
        trace.append("R4 Inter-Agent Conflict")
        return build_verdict(
            "INTER_AGENT_CONFLICT",
            opinions,
            trace,
            reason=(
                "Agents disagree (APPROVE vs REJECT); human arbitration required."
            ),
        )

    # R5 - Unanimous Reject
    if unanimous_reject:
        trace.append("R5 Unanimous Reject")
        return build_verdict(
            "UNANIMOUS_REJECT",
            opinions,
            trace,
            reason="All agents reject the proposal.",
        )

    # R6 - Consensus Approved
    trace.append("R6 Consensus Approved")
    return build_verdict(
        "CONSENSUS_APPROVED",
        opinions,
        trace,
        reason="All agents approve the proposal.",
    )


# =============================================================================
# 7. UI HELPERS (Layer 3)
# =============================================================================

STATUS_BADGE = {
    "APPROVE": "🟢 APPROVE",
    "REJECT": "🔴 REJECT",
    "TIMEOUT": "⚪ TIMEOUT",
}

STATUS_COLOR = {
    "APPROVE": "#1b8a3a",
    "REJECT": "#c62828",
    "TIMEOUT": "#6b7280",
}


# =============================================================================
# 7b. HEADER LINKS & DOCUMENT VIEWER
# =============================================================================

# --- Local resources directory (PDFs read directly from disk) ---------------
RESOURCES_DIR = Path(__file__).resolve().parent / "Ressources"

# --- Preferred port for the optional local static file server ---------------
# NOTE: the static server is now OPTIONAL and never required by the UI. The
# document pages rely on ``st.download_button`` (works everywhere, including
# Streamlit Community Cloud where ``http://localhost`` is unreachable from the
# visitor's browser). The server is kept only for backward compatibility and
# for local "open in a new tab" convenience.
_STATIC_SERVER_PREFERRED_PORT = 8765

# --- Session-state key holding the currently viewed document (or None) ------
DOC_VIEW_KEY = "active_document"

# --- Human-readable titles for each document page ---------------------------
DOCUMENT_TITLES: Dict[str, str] = {
    "cover_letter": "✉️ Cover letter",
    "research_proposal": "🔬 Research Proposal",
    "architectural_blueprint": "🏗️ Architectural Blueprint",
    "technical_statement": "📝 Technical Statement of Purpose & Addendum",
}

# --- Header buttons, in two rows of four (row 1 external, row 2 documents) --
# Each entry: (key, label, kind, target)
#   kind == "document" -> opens an in-app document page (target = doc key)
#   kind == "external" -> opens an external URL in a new tab (target = URL)
HEADER_ROWS = [
    [
        ("diploma", "🏛️ Diploma", "external", "https://e-learning-labo.web.app/diplome"),
        ("resume", "📄 Resume", "external", "https://e-learning-labo.web.app/cv"),
        (
            "linkedin",
            "💼 LinkedIn Profil",
            "external",
            "https://www.linkedin.com/in/filali-salah",
        ),
        (
            "elearning",
            "🎓 E-learning Portfolio",
            "external",
            "https://e-learning-labo.web.app/",
        ),
    ],
    [
        ("cover_letter", "✉️ Cover letter", "document", "cover_letter"),
        (
            "research_proposal",
            "🔬 Research Proposal",
            "document",
            "research_proposal",
        ),
        (
            "architectural_blueprint",
            "🏗️ Architectural Blueprint",
            "document",
            "architectural_blueprint",
        ),
        (
            "technical_statement",
            "📝 Technical Statement",
            "document",
            "technical_statement",
        ),
    ],
]

# --- Mapping document key -> actual PDF filename in Ressources/ -------------
DOCUMENT_FILES: Dict[str, str] = {
    "cover_letter": "Cover Letter.pdf",
    "research_proposal": "Doctoral Research Proposal.pdf",
    "architectural_blueprint": "Academic Contribution Mapping & PhD Core Roadmap.pdf",
    "technical_statement": "Technical Statement of Purpose & Addendum.pdf",
}


@st.cache_resource(show_spinner=False)
def _ensure_static_server() -> Optional[int]:
    """Start a daemon HTTP server serving RESOURCES_DIR once; return its port.

    OPTIONAL / NON-BLOCKING: this helper is no longer required by the UI. It is
    kept for backward compatibility (and local "open in a new tab" convenience)
    but the document pages never depend on it. On Streamlit Community Cloud the
    ``http://localhost`` URLs it produces are unreachable from the visitor's
    browser, so ``st.download_button`` is used instead.

    The server only exposes the ``Ressources/`` directory (never the project
    root). It is started lazily on first use and reused across reruns thanks to
    ``st.cache_resource``. Returns ``None`` if the server cannot be started.
    """
    if not RESOURCES_DIR.is_dir():
        return None

    handler = functools.partial(
        SimpleHTTPRequestHandler, directory=str(RESOURCES_DIR)
    )

    server: Optional[ThreadingHTTPServer] = None
    for port in (_STATIC_SERVER_PREFERRED_PORT, 0):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), handler)
            break
        except OSError:
            server = None
    if server is None:
        return None

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server.server_address[1]


def _resource_url(filename: str) -> Optional[str]:
    """Return an http://localhost URL for a file in Ressources/, or None.

    Best-effort only: returns ``None`` when the optional static server cannot
    be started (e.g. on Streamlit Community Cloud). Callers must treat ``None``
    as a normal, non-fatal condition and fall back to ``st.download_button``.
    """
    try:
        port = _ensure_static_server()
    except Exception:  # pragma: no cover - defensive, never blocks the UI
        return None
    if port is None:
        return None
    return f"http://localhost:{port}/{quote(filename)}"


def _open_document(doc_key: str) -> None:
    """Session-state callback: switch the app to a document page.

    The route is mirrored in the URL query string (?doc=...). This makes the
    page linkable and gives Cloudflare Web Analytics a history change to count
    (the beacon reports pushState/replaceState as a new page view).
    """
    st.session_state[DOC_VIEW_KEY] = doc_key
    st.query_params["doc"] = doc_key


def _close_document() -> None:
    """Session-state callback: return to the home page."""
    st.session_state[DOC_VIEW_KEY] = None
    st.query_params.pop("doc", None)


def render_header_links() -> None:
    """Render the header buttons in two rows of four equal-width columns."""
    for row in HEADER_ROWS:
        cols = st.columns(len(row))
        for col, (key, label, kind, target) in zip(cols, row):
            with col:
                if kind == "external":
                    st.link_button(label, target, use_container_width=True)
                else:
                    st.button(
                        label,
                        key=f"header_btn_{key}",
                        on_click=_open_document,
                        args=(target,),
                        use_container_width=True,
                    )


def render_document_page(doc_key: str) -> None:
    """Render a document page with an inline PDF preview and a Download button.

    The download button streams the PDF bytes straight from ``Ressources/`` so
    it works identically locally and on Streamlit Community Cloud. The PDF is
    also rendered inline via ``st.pdf`` (from the ``streamlit[pdf]`` extra); if
    the viewer is unavailable, a base64 ``data:`` iframe keeps the page usable.
    """
    title = DOCUMENT_TITLES.get(doc_key, "Document")
    filename = DOCUMENT_FILES.get(doc_key)
    file_path = RESOURCES_DIR / filename if filename else None

    st.button("← Back to home", on_click=_close_document)
    st.title(title)

    if file_path is None or not file_path.is_file():
        st.error(
            f"The document file could not be found in the Ressources folder "
            f"({filename})."
        )
        return

    # Read bytes once: this is the single source of truth for the download.
    try:
        pdf_bytes = file_path.read_bytes()
    except OSError as exc:
        st.error(f"Unable to read the document: {exc}")
        return

    # Primary action: native Streamlit download (works everywhere).
    st.download_button(
        f"Download {filename}",
        data=pdf_bytes,
        file_name=filename,
        mime="application/pdf",
        use_container_width=True,
        key=f"download_doc_{doc_key}",
    )

    # Render the PDF inline so visitors can read it without leaving the app.
    try:
        st.pdf(pdf_bytes, height=900, alt=f"Preview of {filename}")
    except Exception:
        # Fallback when streamlit-pdf is unavailable: embed a data: URL iframe.
        b64 = base64.b64encode(pdf_bytes).decode("ascii")
        iframe = (
            '<iframe src="data:application/pdf;base64,'
            + b64
            + '" width="100%" height="900" style="border:0;"></iframe>'
        )
        components.html(iframe, height=900, scrolling=True)


def render_sidebar() -> Dict[str, Any]:
    """Render the sidebar controls and return the selected configuration."""
    # --- Execution mode (rendered first, above the scenario group) ----------
    mode_label = st.sidebar.radio(
        "Execution mode",
        options=["Instant Offline Simulation", "Live DeepSeek LLM"],
        index=0,
        help=(
            "Offline uses pre-computed opinions (reproducible). Live queries "
            "DeepSeek via the OpenAI-compatible API."
        ),
    )
    mode = "offline" if mode_label == "Instant Offline Simulation" else "live"

    run = st.sidebar.button(
        "Run Arbitration",
        type="primary",
        use_container_width=True,
        help=(
            "Relance l'arbitrage multi-agents selon le mode d'exécution sélectionné. "
            "Utile lorsque vous basculez entre 'Instant Offline Simulation' (résultats "
            "pré-calculés, reproductibles) et 'Live DeepSeek LLM' (appel réel à l'API)."
        ),
    )

    api_key = ""
    if mode == "live":
        api_key = st.sidebar.text_input(
            "DeepSeek API key",
            type="password",
            help="Used only in Live mode; never persisted to disk.",
        )

    st.sidebar.markdown("---")

    # --- Scenario group (rendered below the execution mode) -----------------
    scenario_key = st.sidebar.selectbox(
        "Scenario",
        options=list(SCENARIOS.keys()),
        index=0,
    )
    scenario = SCENARIOS[scenario_key]

    st.sidebar.caption(f"Context: {scenario['context']}")

    crash_sim = False
    if scenario.get("supports_crash_sim"):
        crash_sim = st.sidebar.checkbox(
            "Simulate Financial Agent Timeout/Crash",
            value=False,
            key="crash_sim_active",
            help="Forces the Financial_Risk_Agent into a TIMEOUT to demo graceful degradation.",
        )
    else:
        st.sidebar.checkbox(
            "Simulate Financial Agent Timeout/Crash",
            value=False,
            key="crash_sim_disabled",
            disabled=True,
            help="Only available for Scenario 3 (Graceful Degradation).",
        )

    st.sidebar.divider()
    st.sidebar.caption(IP_NOTICE)

    return {
        "mode": mode,
        "scenario_key": scenario_key,
        "crash_sim": crash_sim,
        "api_key": api_key,
        "run": run,
    }


def render_agent_card(agent_name: str, opinion: Dict[str, Any]) -> None:
    """Render a single agent card (status badge, confidence, proposal, reasoning)."""
    status = opinion["status"]
    confidence = opinion["confidence"]
    badge = STATUS_BADGE.get(status, status)
    color = STATUS_COLOR.get(status, "#6b7280")

    st.markdown(f"#### {agent_name}")
    if agent_name == AGENT_REGULATORY:
        st.markdown(
            "<span style='background:#c62828;color:white;padding:2px 8px;"
            "border-radius:6px;font-size:0.75rem;font-weight:600;'>VETO POWER</span>",
            unsafe_allow_html=True,
        )

    st.markdown(
        f"<span style='color:{color};font-weight:700;font-size:1.05rem;'>{badge}</span>",
        unsafe_allow_html=True,
    )
    st.caption(f"Confidence: {confidence:.2f}")
    st.progress(confidence)

    st.markdown("**Proposal**")
    st.write(opinion["proposal"])

    with st.expander("Reasoning"):
        st.write(opinion["reasoning"])


def render_verdict_box(verdict: Dict[str, Any]) -> None:
    """Render the arbitration verdict box, colored by severity."""
    severity = verdict["severity"]
    code = verdict["verdict"]

    header = f"**Verdict: `{code}`** — Severity: **{severity}**"
    body = (
        f"\n\n**Recommended action:** {verdict['recommended_action']}"
        f"\n\n**Reason:** {verdict['reason']}"
        f"\n\n**Human escalation required:** "
        f"{'Yes' if verdict['requires_human'] else 'No'}"
    )

    if severity == "INFO":
        st.success(header + body)
    elif severity == "WARNING":
        st.warning(header + body)
    else:  # CRITICAL
        st.error(header + body)

    # Explicit graceful-degradation warning if any agent timed out.
    timed_out = [
        a for a, o in verdict["opinions"].items() if o["status"] == "TIMEOUT"
    ]
    if timed_out:
        st.warning(
            "**Graceful Degradation:** "
            f"{len(timed_out)} agent(s) unavailable "
            f"({', '.join(timed_out)}). The verdict was computed on the active "
            "agents only."
        )

    with st.expander("Rule trace (glass-box)"):
        for step in verdict["trace"]:
            st.write(f"- {step}")


def append_audit_entry(
    scenario_id: str,
    mode: str,
    ai_verdict: str,
    human_decision: str,
    justification: str,
    agent_statuses: Dict[str, str],
) -> None:
    """Append an immutable, timestamped entry to the session audit log."""
    log: List[Dict[str, Any]] = st.session_state.setdefault("audit_log", [])
    entry_id = len(log) + 1
    log.append(
        {
            "entry_id": entry_id,
            "timestamp_utc": utc_now_iso(),
            "scenario": scenario_id,
            "mode": mode,
            "ai_verdict": ai_verdict,
            "human_decision": human_decision,
            "justification": justification,
            "agent_statuses": dict(agent_statuses),
        }
    )


def render_contest_form(verdict: Dict[str, Any], scenario_id: str, mode: str) -> None:
    """Render the Contest & Overrule form and commit decisions to the audit log."""
    st.subheader("Contest & Overrule Decision")

    with st.form("contest_form", clear_on_submit=False):
        decision = st.radio(
            "Your decision",
            options=["Accept AI Verdict", "Overrule & Force Manual Action"],
            index=0,
        )
        justification = st.text_area(
            "Justification (required when overruling)",
            placeholder="Explain the rationale for your decision...",
        )
        submitted = st.form_submit_button("Commit to Audit Log", type="primary")

    if submitted:
        if decision == "Overrule & Force Manual Action" and not justification.strip():
            st.error("A justification is mandatory when overruling the AI verdict.")
            return

        agent_statuses = {
            agent: opinion["status"]
            for agent, opinion in verdict["opinions"].items()
        }
        append_audit_entry(
            scenario_id=scenario_id,
            mode=mode,
            ai_verdict=verdict["verdict"],
            human_decision=decision,
            justification=justification.strip() or "—",
            agent_statuses=agent_statuses,
        )
        st.success("Decision committed to the immutable audit log.")


def render_audit_log() -> None:
    """Render the append-only audit log, most recent entry first."""
    st.subheader("Immutable Audit Log")
    log: List[Dict[str, Any]] = st.session_state.get("audit_log", [])
    if not log:
        st.info("No audit entries yet. Run an arbitration and commit a decision.")
        return

    # Most recent first (display only; storage remains append-only).
    display_rows = list(reversed(log))
    st.dataframe(display_rows, use_container_width=True)

    with st.expander("Raw JSON audit log"):
        st.json(log)


# =============================================================================
# 8. MAIN ORCHESTRATION
# =============================================================================

def main() -> None:
    st.set_page_config(
        page_title="AURA-Grid Research Prototype & Architectural Demonstrator",
        page_icon="⚡",
        layout="wide",
    )

    # --- Cloudflare Web Analytics ------------------------------------------
    # Injected first (right after set_page_config) so the beacon loads on every
    # page/view of the app, including the document pages that early-return.
    _inject_cloudflare_analytics()

    # --- Global layout: remove the large empty band at the top -------------
    # Streamlit reserves a tall header plus a ~6rem top padding on the main
    # block container, which leaves a big blank area above the first rendered
    # element (the IP notice). We shrink both so the content rises
    # proportionally. The header is only made shorter (not hidden) so its
    # controls (sidebar toggle, menu) stay visible and clickable.
    st.markdown(
        """
        <style>
        /* Main content container: drastically reduce the default top padding */
        .block-container,
        [data-testid="stMainBlockContainer"] {
            padding-top: 2.25rem !important;
            padding-bottom: 0 !important;
        }

        /* Header: shorter bar instead of hiding it (keeps its controls usable) */
        header[data-testid="stHeader"] {
            height: 2rem;
            min-height: 2rem;
            background: transparent;
        }

        /* Remove any residual padding on the scrollable main view */
        [data-testid="stAppViewContainer"] > .main {
            padding-top: 0 !important;
        }

        /* Hide Streamlit Cloud toolbar actions (deploy/share buttons that link
           to streamlit.io/cloud and share.streamlit.io). The header and sidebar
           toggle are intentionally left visible. */
        [data-testid="stToolbarActions"],
        [data-testid="stAppDeployButton"],
        [data-testid="stStatusWidget"],
        a[href*="streamlit.io/cloud"],
        a[href*="share.streamlit.io/user/"] {
            display: none !important;
        }

        /* --- Download buttons: dark pink + blinking green arrow ---------- */
        [data-testid="stDownloadButton"] button {
            background-color: #c2185b !important;
            border: 1px solid #c2185b !important;
            color: #ffffff !important;
            box-shadow: 0 2px 10px rgba(194, 24, 91, 0.35);
            transition: background-color 120ms ease, border-color 120ms ease,
                box-shadow 120ms ease;
        }

        [data-testid="stDownloadButton"] button:hover {
            background-color: #ad1457 !important;
            border-color: #ad1457 !important;
            color: #ffffff !important;
            box-shadow: 0 3px 14px rgba(173, 20, 87, 0.5);
        }

        [data-testid="stDownloadButton"] button:active,
        [data-testid="stDownloadButton"] button:focus:not(:active) {
            background-color: #880e4f !important;
            border-color: #880e4f !important;
            color: #ffffff !important;
        }

        [data-testid="stDownloadButton"] button p {
            color: #ffffff !important;
        }

        /* Blinking green arrow, inserted before the button text without
           changing the accessible label ("Download <filename>"). */
        [data-testid="stDownloadButton"] button p::before {
            content: "↓";
            display: inline-block;
            margin-right: 0.5rem;
            color: #00e676;
            font-size: 1.2em;
            font-weight: 900;
            line-height: 1;
            text-shadow: 0 0 6px rgba(0, 230, 118, 0.9);
            animation: dl-arrow-blink 1s ease-in-out infinite;
        }

        @keyframes dl-arrow-blink {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.1; }
        }

        @media (prefers-reduced-motion: reduce) {
            [data-testid="stDownloadButton"] button p::before {
                animation: none;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # --- Session state initialization --------------------------------------
    if "audit_log" not in st.session_state:
        st.session_state["audit_log"] = []
    if "last_verdict" not in st.session_state:
        st.session_state["last_verdict"] = None
    if "last_run_key" not in st.session_state:
        st.session_state["last_run_key"] = None

    # Keep the document route in sync with ?doc=... so browser back/forward and
    # shared links land on the correct page (and each view is a tracked
    # Cloudflare history change).
    requested_document = st.query_params.get("doc")
    if (
        not isinstance(requested_document, str)
        or requested_document not in DOCUMENT_FILES
    ):
        requested_document = None
    if DOC_VIEW_KEY not in st.session_state:
        st.session_state[DOC_VIEW_KEY] = requested_document
    elif st.session_state[DOC_VIEW_KEY] != requested_document:
        st.session_state[DOC_VIEW_KEY] = requested_document

    st.caption(IP_NOTICE)

    # --- Document page routing ---------------------------------------------
    # When a document is selected from the header, show its dedicated page and
    # stop here so the rest of the dashboard is not rendered on that view.
    active_document = st.session_state.get(DOC_VIEW_KEY)
    if active_document:
        render_document_page(active_document)
        st.divider()
        st.caption(IP_NOTICE)
        return

    # --- Header buttons (two rows of four) ---------------------------------
    render_header_links()

    # --- Title and subtitle (restored below the header buttons) ------------
    st.subheader("⚡ AURA-Grid Research Prototype & Architectural Demonstrator")
    st.caption(
        "Auditable & Reliable Multi-Agent System for Regulated Energy Operations"
    )

    config = render_sidebar()
    mode = config["mode"]
    scenario_key = config["scenario_key"]
    crash_sim = config["crash_sim"]
    api_key = config["api_key"]
    scenario = SCENARIOS[scenario_key]

    # A run is triggered by the button, or automatically when the configuration
    # changes (so the demo is instantly responsive).
    run_key = (mode, scenario_key, crash_sim)
    should_run = config["run"] or st.session_state["last_run_key"] != run_key

    if should_run:
        if mode == "live" and not api_key.strip():
            st.warning(
                "Live mode requires a DeepSeek API key. Enter it in the sidebar "
                "or switch to Instant Offline Simulation."
            )
            st.session_state["last_verdict"] = None
            st.session_state["last_run_key"] = run_key
        else:
            with st.spinner("Collecting agent opinions..."):
                if mode == "offline":
                    raw_opinions = get_offline_opinions(scenario_key, crash_sim)
                else:
                    raw_opinions = get_live_opinions(scenario_key, api_key.strip())

            # Normalize every opinion through the kernel's validation layer.
            opinions = {
                agent: normalize_opinion(agent, raw_opinions.get(agent))
                for agent in AGENT_ORDER
            }

            verdict = arbitrate(opinions)
            st.session_state["last_verdict"] = verdict
            st.session_state["last_run_key"] = run_key

            # Automatically log the AI verdict (append-only).
            agent_statuses = {
                agent: opinion["status"] for agent, opinion in opinions.items()
            }
            append_audit_entry(
                scenario_id=scenario["id"],
                mode=mode,
                ai_verdict=verdict["verdict"],
                human_decision="AI Verdict (auto-logged)",
                justification="Automatic arbitration result.",
                agent_statuses=agent_statuses,
            )

    verdict = st.session_state.get("last_verdict")

    if verdict is None:
        st.info(
            "Select a scenario and click **Run Arbitration** to begin. "
            "Offline mode works without any API key."
        )
        render_audit_log()
        st.divider()
        st.caption(IP_NOTICE)
        return

    # --- Layer 1 display: agent cards --------------------------------------
    st.subheader("Agent Opinions")
    cols = st.columns(3)
    for col, agent_name in zip(cols, AGENT_ORDER):
        with col:
            render_agent_card(agent_name, verdict["opinions"][agent_name])

    st.markdown("---")

    # --- Layer 2 display: verdict box --------------------------------------
    st.subheader("Arbitration Verdict")
    render_verdict_box(verdict)

    st.markdown("---")

    # --- Layer 3 display: HITL form + audit log ----------------------------
    render_contest_form(verdict, scenario["id"], mode)

    st.markdown("---")
    render_audit_log()

    st.divider()
    st.caption(IP_NOTICE)


if __name__ == "__main__":
    main()
