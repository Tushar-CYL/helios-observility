"""Reasoners — turn grounded candidates into a narrated RCA report.

Two implementations share the same grounded candidate set:

* :class:`HeuristicReasoner` — deterministic, no LLM. Always available; used for
  tests and as a fallback. Produces a correct, evidence-cited report.
* :class:`OllamaReasoner` — sends the candidates + run facts to a local LLM
  (Ollama) and asks only for a natural-language *summary* and *suggested fix*.
  The ranked causes and probabilities still come from the detectors, so the LLM
  cannot invent a root cause — it can only explain the evidence we found.

Privacy-first: OllamaReasoner talks to a local Ollama endpoint; nothing leaves
the host. If Ollama is unreachable, the service falls back to the heuristic.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field

from .detectors import Candidate, detect_candidates, normalize
from .retrieval import RunContext


@dataclass
class Cause:
    hypothesis: str
    probability: float
    evidence: list[str] = field(default_factory=list)


@dataclass
class RcaReport:
    summary: str
    causes: list[Cause]
    suggested_fix: str
    confidence: float
    reasoner: str
    model: str = ""
    evidence_refs: list[str] = field(default_factory=list)

    def to_row_fields(self) -> dict:
        return {
            "summary": self.summary,
            "causes": json.dumps([asdict(c) for c in self.causes]),
            "suggested_fix": self.suggested_fix,
            "confidence": round(float(self.confidence), 3),
            "reasoner": self.reasoner,
            "model": self.model,
            "evidence_refs": json.dumps(self.evidence_refs),
        }


_FIXES = {
    "stale_after_timeout": (
        "Fail loudly when the authoritative MCP source is unavailable instead of "
        "silently using cached data; add a freshness/TTL guard on cache reads."
    ),
    "stale_memory": (
        "Add a TTL / max-age check on memory reads and invalidate entries older "
        "than the policy window before using them in a prompt."
    ),
    "mcp_timeout": (
        "Add retries with backoff and a circuit breaker for the failing MCP "
        "server, and surface its unavailability rather than degrading silently."
    ),
    "permission_change": (
        "Audit and pin the MCP permission scope; alert on unexpected scope changes."
    ),
    "no_eval_gate": (
        "Add an output evaluation gate that blocks or flags low-confidence answers "
        "before they reach the user."
    ),
}


def _ranked(ctx: RunContext) -> tuple[list[Candidate], list[Cause], list[str], float]:
    candidates = detect_candidates(ctx)
    pairs = normalize(candidates)
    causes = [
        Cause(hypothesis=c.hypothesis, probability=p, evidence=c.evidence)
        for c, p in pairs
    ]
    refs: list[str] = []
    for c in candidates:
        refs.extend(c.evidence_refs)
    top = pairs[0][1] if pairs else 0.0
    return candidates, causes, refs, top


class HeuristicReasoner:
    name = "heuristic"

    def analyze(self, ctx: RunContext) -> RcaReport:
        candidates, causes, refs, top = _ranked(ctx)
        if not candidates:
            return RcaReport(
                summary="No known failure signals were detected in this run.",
                causes=[],
                suggested_fix="",
                confidence=0.0,
                reasoner=self.name,
            )
        lead = candidates[0]
        outcome = "produced an incorrect answer" if ctx.run.get("outcome_correct") == 0 else "completed"
        summary = (
            f"The run '{ctx.run.get('agent_name', 'agent')}' {outcome}. "
            f"Most likely root cause: {lead.hypothesis}"
        )
        fix = _FIXES.get(lead.code, "Investigate the leading hypothesis above.")
        return RcaReport(
            summary=summary,
            causes=causes,
            suggested_fix=fix,
            confidence=top,
            reasoner=self.name,
            evidence_refs=refs,
        )


class OllamaReasoner:
    name = "ollama"

    def __init__(self, endpoint: str, model: str, timeout: float = 60.0) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._timeout = timeout

    def analyze(self, ctx: RunContext) -> RcaReport:
        candidates, causes, refs, top = _ranked(ctx)
        if not candidates:
            return HeuristicReasoner().analyze(ctx)

        prompt = self._build_prompt(ctx, causes)
        try:
            text = self._generate(prompt)
            parsed = json.loads(text)
            summary = str(parsed.get("summary", "")).strip()
            fix = str(parsed.get("suggested_fix", "")).strip()
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
            # Local LLM unreachable or returned unparseable output -> fall back.
            return HeuristicReasoner().analyze(ctx)

        if not summary or not fix:
            return HeuristicReasoner().analyze(ctx)

        return RcaReport(
            summary=summary,
            causes=causes,  # probabilities stay grounded in detectors
            suggested_fix=fix,
            confidence=top,
            reasoner=self.name,
            model=self._model,
            evidence_refs=refs,
        )

    def _build_prompt(self, ctx: RunContext, causes: list[Cause]) -> str:
        facts = {
            "agent": ctx.run.get("agent_name"),
            "framework": ctx.run.get("agent_framework"),
            "question": ctx.run.get("input"),
            "answer": ctx.run.get("output"),
            "outcome_correct": ctx.run.get("outcome_correct"),
            "ranked_candidate_causes": [
                {"hypothesis": c.hypothesis, "probability": c.probability, "evidence": c.evidence}
                for c in causes
            ],
        }
        return (
            "You are an SRE assistant for AI agent systems. Below are the FACTS and "
            "the RANKED CANDIDATE CAUSES already derived from telemetry. Do NOT "
            "invent new causes or change the probabilities. Write a concise "
            "explanation grounded ONLY in these facts.\n\n"
            f"FACTS:\n{json.dumps(facts, indent=2, default=str)}\n\n"
            "Respond with STRICT JSON only, no prose, in this shape:\n"
            '{"summary": "<2-3 sentence plain-language root-cause explanation>", '
            '"suggested_fix": "<1-2 concrete remediation steps>"}'
        )

    def _generate(self, prompt: str) -> str:
        payload = json.dumps(
            {
                "model": self._model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.1},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self._endpoint}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body.get("response", "")
