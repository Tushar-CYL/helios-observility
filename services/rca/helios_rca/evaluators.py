"""Evaluators — quality/eval scoring for an agent run.

Three kinds, mirroring the V2 plan:

* **code** — deterministic checks over telemetry (grounding, staleness,
  tool-failure, latency budget). No LLM; always available; fully testable.
* **llm_judge** — a local LLM (Ollama) grades the answer for faithfulness to the
  retrieved evidence. Falls back to a code heuristic if Ollama is unreachable.
* **drift** — compares the run's answer against a reference/expected value when
  one is known, to flag content drift.

Each evaluator returns an :class:`EvalOutcome`. The engine persists them to
``helios.eval_result`` and the dashboards/RCA consume them.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .retrieval import RunContext


@dataclass
class EvalOutcome:
    evaluator: str
    evaluator_kind: str  # code | llm_judge | drift
    passed: bool
    score: float  # 0..1
    label: str = ""
    reason: str = ""
    reasoner: str = "heuristic"
    model: str = ""


CodeEvaluator = Callable[[RunContext], "EvalOutcome | None"]
_CODE_EVALUATORS: list[CodeEvaluator] = []


def code_evaluator(fn: CodeEvaluator) -> CodeEvaluator:
    _CODE_EVALUATORS.append(fn)
    return fn


def _num(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --- Code / heuristic evaluators ------------------------------------------
@code_evaluator
def answer_grounded(ctx: RunContext) -> EvalOutcome:
    """Was the answer grounded in fresh evidence, or did it rely on stale memory?"""
    stale_reads = [m for m in ctx.memory if m.get("is_stale") in (1, True)]
    if stale_reads:
        return EvalOutcome(
            evaluator="answer_grounded",
            evaluator_kind="code",
            passed=False,
            score=0.2,
            label="ungrounded",
            reason=(
                f"Answer relied on {len(stale_reads)} stale memory read(s); "
                f"not grounded in current authoritative data."
            ),
        )
    return EvalOutcome(
        evaluator="answer_grounded",
        evaluator_kind="code",
        passed=True,
        score=1.0,
        label="grounded",
        reason="No stale memory influenced the answer.",
    )


@code_evaluator
def tool_reliability(ctx: RunContext) -> EvalOutcome:
    total = len(ctx.mcp)
    if total == 0:
        return EvalOutcome("tool_reliability", "code", True, 1.0, "n/a", "No MCP calls.")
    errors = sum(1 for m in ctx.mcp if m.get("status") == "error")
    score = round(1 - errors / total, 3)
    return EvalOutcome(
        evaluator="tool_reliability",
        evaluator_kind="code",
        passed=errors == 0,
        score=score,
        label="ok" if errors == 0 else "degraded",
        reason=f"{errors}/{total} MCP call(s) failed.",
    )


@code_evaluator
def latency_budget(ctx: RunContext, budget_ms: float = 4000) -> EvalOutcome:
    worst = max((_num(m.get("latency_ms")) for m in ctx.mcp), default=0.0)
    passed = worst <= budget_ms
    score = round(max(0.0, min(1.0, 1 - (worst - budget_ms) / budget_ms)), 3) if not passed else 1.0
    return EvalOutcome(
        evaluator="latency_budget",
        evaluator_kind="code",
        passed=passed,
        score=score,
        label="within_budget" if passed else "over_budget",
        reason=f"Worst MCP latency {int(worst)} ms vs budget {int(budget_ms)} ms.",
    )


def run_code_evaluators(ctx: RunContext) -> list[EvalOutcome]:
    return [fn(ctx) for fn in _CODE_EVALUATORS]


# --- Drift evaluator -------------------------------------------------------
def evaluate_drift(ctx: RunContext, expected: str | None) -> EvalOutcome | None:
    """Compare the answer to a known expected value, if provided."""
    if not expected:
        return None
    answer = (ctx.run.get("output") or "").strip().lower()
    exp = expected.strip().lower()
    matched = exp in answer or answer in exp
    return EvalOutcome(
        evaluator="content_drift",
        evaluator_kind="drift",
        passed=matched,
        score=1.0 if matched else 0.0,
        label="stable" if matched else "drifted",
        reason=(
            "Answer matches the expected reference."
            if matched
            else f"Answer diverged from expected reference '{expected}'."
        ),
    )


# --- LLM-as-judge evaluator ------------------------------------------------
class LlmJudge:
    def __init__(self, endpoint: str, model: str, timeout: float = 60.0) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._timeout = timeout

    def evaluate(self, ctx: RunContext) -> EvalOutcome:
        evidence = self._evidence(ctx)
        prompt = (
            "You are a strict evaluator. Judge whether the ASSISTANT ANSWER is "
            "faithful to and supported by the EVIDENCE actually retrieved during "
            "the run. If the answer relies on outdated or unsupported facts, it is "
            "NOT faithful. Respond with STRICT JSON only:\n"
            '{"faithful": true|false, "score": <0..1>, "reason": "<one sentence>"}\n\n'
            f"QUESTION: {ctx.run.get('input')}\n"
            f"ASSISTANT ANSWER: {ctx.run.get('output')}\n"
            f"EVIDENCE:\n{json.dumps(evidence, indent=2, default=str)}\n"
        )
        try:
            raw = self._generate(prompt)
            parsed = json.loads(raw)
            faithful = bool(parsed.get("faithful"))
            score = float(parsed.get("score", 0.0))
            reason = str(parsed.get("reason", "")).strip()
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
            return self._fallback(ctx)
        if not reason:
            return self._fallback(ctx)
        return EvalOutcome(
            evaluator="answer_faithfulness",
            evaluator_kind="llm_judge",
            passed=faithful,
            score=round(max(0.0, min(1.0, score)), 3),
            label="faithful" if faithful else "hallucinated",
            reason=reason,
            reasoner="ollama",
            model=self._model,
        )

    def _evidence(self, ctx: RunContext) -> dict:
        return {
            "memory_reads": [
                {
                    "key": m.get("key"),
                    "content": m.get("content"),
                    "is_stale": m.get("is_stale"),
                    "age_days": round(_num(m.get("age_ms")) / 86_400_000, 1),
                }
                for m in ctx.memory
            ],
            "mcp_calls": [
                {"server": m.get("server_name"), "tool": m.get("tool_name"), "status": m.get("status")}
                for m in ctx.mcp
            ],
        }

    def _fallback(self, ctx: RunContext) -> EvalOutcome:
        # Heuristic stand-in when the local LLM is unavailable.
        base = answer_grounded(ctx)
        return EvalOutcome(
            evaluator="answer_faithfulness",
            evaluator_kind="llm_judge",
            passed=base.passed,
            score=base.score,
            label="faithful" if base.passed else "hallucinated",
            reason=f"(heuristic fallback) {base.reason}",
            reasoner="heuristic",
        )

    def _generate(self, prompt: str) -> str:
        payload = json.dumps(
            {
                "model": self._model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self._endpoint}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return json.loads(resp.read().decode("utf-8")).get("response", "")
