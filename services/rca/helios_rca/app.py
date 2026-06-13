"""HELIOS RCA service — the AI Ops Copilot.

FastAPI app exposing:

    GET  /healthz            liveness + reasoner availability
    POST /rca/{run_id}       analyze a run, persist the report, return it
    GET  /rca/{run_id}       latest stored report for a run (if any)
    POST /eval/{run_id}      run evaluations on a run, persist + return
    GET  /eval/{run_id}      latest stored evaluations for a run
    POST /triage/{run_id}    eval + auto-trigger RCA when the run looks bad

Results are written to ``helios.rca_report`` / ``helios.eval_result`` and
surfaced in Grafana. No UI here by design — Grafana is the front end.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .clickhouse import ClickHouse
from .config import settings
from .correlation import correlate
from .evaluators import (
    EvalOutcome,
    LlmJudge,
    evaluate_drift,
    run_code_evaluators,
)
from .reasoner import HeuristicReasoner, OllamaReasoner, RcaReport
from .retrieval import RunContext, load_run_context

app = FastAPI(title="HELIOS RCA Copilot", version="0.2.0")


def _clickhouse() -> ClickHouse:
    return ClickHouse(
        url=settings.clickhouse_url,
        database=settings.clickhouse_db,
        user=settings.clickhouse_user,
        password=settings.clickhouse_password,
    )


def _ollama_reachable() -> bool:
    try:
        with urllib.request.urlopen(f"{settings.ollama_endpoint}/api/tags", timeout=3):
            return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _select_reasoner():
    choice = settings.reasoner
    if choice == "heuristic":
        return HeuristicReasoner()
    if choice == "ollama" or (choice == "auto" and _ollama_reachable()):
        return OllamaReasoner(
            endpoint=settings.ollama_endpoint,
            model=settings.ollama_model,
            timeout=settings.ollama_timeout,
        )
    return HeuristicReasoner()


class CauseModel(BaseModel):
    hypothesis: str
    probability: float
    evidence: list[str] = []


class RcaResponse(BaseModel):
    run_id: str
    report_id: str
    trigger: str
    summary: str
    causes: list[CauseModel]
    suggested_fix: str
    confidence: float
    reasoner: str
    model: str = ""
    evidence_refs: list[str] = []
    latency_ms: int


@app.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "reasoner_config": settings.reasoner,
        "ollama_reachable": _ollama_reachable(),
        "ollama_model": settings.ollama_model,
    }


def _persist(ch: ClickHouse, ctx_run: dict, report: RcaReport, trigger: str, latency_ms: int) -> str:
    report_id = str(uuid4())
    row = {
        "schema_version": ctx_run.get("schema_version", "1.0.0"),
        "tenant_id": ctx_run.get("tenant_id", "default"),
        "agent_run_id": ctx_run["agent_run_id"],
        "report_id": report_id,
        "trigger": trigger,
        "latency_ms": latency_ms,
    }
    row.update(report.to_row_fields())
    ch.insert_json("rca_report", [row])
    return report_id


@app.post("/rca/{run_id}", response_model=RcaResponse)
def analyze(run_id: str, trigger: str = "on_demand") -> RcaResponse:
    ch = _clickhouse()
    ctx = load_run_context(ch, run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"agent_run '{run_id}' not found")
    return _analyze_ctx(ch, ctx, run_id, trigger)


def _analyze_ctx(ch: ClickHouse, ctx: RunContext, run_id: str, trigger: str) -> RcaResponse:
    start = time.perf_counter()
    report = _select_reasoner().analyze(ctx)
    latency_ms = round((time.perf_counter() - start) * 1000)
    report_id = _persist(ch, ctx.run, report, trigger, latency_ms)

    return RcaResponse(
        run_id=run_id,
        report_id=report_id,
        trigger=trigger,
        summary=report.summary,
        causes=[CauseModel(**c.__dict__) for c in report.causes],
        suggested_fix=report.suggested_fix,
        confidence=report.confidence,
        reasoner=report.reasoner,
        model=report.model,
        evidence_refs=report.evidence_refs,
        latency_ms=latency_ms,
    )


@app.get("/rca/{run_id}", response_model=RcaResponse)
def latest(run_id: str) -> RcaResponse:
    ch = _clickhouse()
    rid = run_id.replace("'", "''")
    rows = ch.query_json(
        "SELECT *, toString(agent_run_id) AS agent_run_id, "
        "toString(report_id) AS report_id "
        f"FROM helios.rca_report WHERE agent_run_id = '{rid}' "
        "ORDER BY created_at DESC LIMIT 1"
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"no RCA report for run '{run_id}'")
    import json as _json

    r = rows[0]
    causes = [CauseModel(**c) for c in _json.loads(r.get("causes") or "[]")]
    return RcaResponse(
        run_id=run_id,
        report_id=r["report_id"],
        trigger=r.get("trigger", "on_demand"),
        summary=r.get("summary", ""),
        causes=causes,
        suggested_fix=r.get("suggested_fix", ""),
        confidence=float(r.get("confidence", 0) or 0),
        reasoner=r.get("reasoner", ""),
        model=r.get("model", ""),
        evidence_refs=_json.loads(r.get("evidence_refs") or "[]"),
        latency_ms=int(r.get("latency_ms", 0) or 0),
    )


# --- Evaluation engine -----------------------------------------------------
class EvalModel(BaseModel):
    evaluator: str
    evaluator_kind: str
    passed: bool
    score: float
    label: str = ""
    reason: str = ""
    reasoner: str = "heuristic"
    model: str = ""


class EvalResponse(BaseModel):
    run_id: str
    evaluations: list[EvalModel]
    overall_score: float
    all_passed: bool


def _judge() -> LlmJudge | None:
    if settings.reasoner == "heuristic":
        return None
    if settings.reasoner == "ollama" or (settings.reasoner == "auto" and _ollama_reachable()):
        return LlmJudge(settings.ollama_endpoint, settings.ollama_model, settings.ollama_timeout)
    return None


def _run_evaluations(ctx: RunContext, expected: str | None) -> list[EvalOutcome]:
    outcomes = run_code_evaluators(ctx)
    drift = evaluate_drift(ctx, expected)
    if drift is not None:
        outcomes.append(drift)
    judge = _judge()
    outcomes.append(judge.evaluate(ctx) if judge else LlmJudge("", "")._fallback(ctx))
    return outcomes


def _persist_evals(ch: ClickHouse, ctx_run: dict, outcomes: list[EvalOutcome]) -> None:
    rows = []
    for o in outcomes:
        rows.append(
            {
                "schema_version": ctx_run.get("schema_version", "1.0.0"),
                "tenant_id": ctx_run.get("tenant_id", "default"),
                "agent_run_id": ctx_run["agent_run_id"],
                "eval_result_id": str(uuid4()),
                "evaluator": o.evaluator,
                "evaluator_kind": o.evaluator_kind,
                "passed": 1 if o.passed else 0,
                "score": round(float(o.score), 3),
                "label": o.label,
                "reason": o.reason,
                "reasoner": o.reasoner,
                "model": o.model,
            }
        )
    ch.insert_json("eval_result", rows)


def _eval_response(run_id: str, outcomes: list[EvalOutcome]) -> EvalResponse:
    scores = [o.score for o in outcomes] or [0.0]
    return EvalResponse(
        run_id=run_id,
        evaluations=[EvalModel(**o.__dict__) for o in outcomes],
        overall_score=round(sum(scores) / len(scores), 3),
        all_passed=all(o.passed for o in outcomes),
    )


@app.post("/eval/{run_id}", response_model=EvalResponse)
def evaluate(run_id: str, expected: str | None = None) -> EvalResponse:
    ch = _clickhouse()
    ctx = load_run_context(ch, run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"agent_run '{run_id}' not found")
    outcomes = _run_evaluations(ctx, expected)
    _persist_evals(ch, ctx.run, outcomes)
    return _eval_response(run_id, outcomes)


@app.get("/eval/{run_id}", response_model=EvalResponse)
def latest_evals(run_id: str) -> EvalResponse:
    ch = _clickhouse()
    rid = run_id.replace("'", "''")
    rows = ch.query_json(
        "SELECT evaluator, evaluator_kind, passed, score, label, reason, reasoner, model "
        "FROM helios.eval_result "
        f"WHERE agent_run_id = '{rid}' AND created_at = ("
        f"  SELECT max(created_at) FROM helios.eval_result WHERE agent_run_id = '{rid}')"
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"no evaluations for run '{run_id}'")
    outcomes = [
        EvalOutcome(
            evaluator=r["evaluator"],
            evaluator_kind=r["evaluator_kind"],
            passed=bool(int(r["passed"])),
            score=float(r["score"]),
            label=r.get("label", ""),
            reason=r.get("reason", ""),
            reasoner=r.get("reasoner", "heuristic"),
            model=r.get("model", ""),
        )
        for r in rows
    ]
    return _eval_response(run_id, outcomes)


# --- Auto-triage: evaluate, then auto-trigger RCA when a run looks bad ------
class TriageResponse(BaseModel):
    run_id: str
    evaluation: EvalResponse
    rca_triggered: bool
    rca: RcaResponse | None = None
    reason: str


@app.post("/triage/{run_id}", response_model=TriageResponse)
def triage(run_id: str, expected: str | None = None, score_threshold: float = 0.7) -> TriageResponse:
    """Evaluate a run and auto-trigger RCA if it failed evals or its outcome was wrong.

    This is the closed loop: a finished run is graded, and when quality is poor
    the Copilot is invoked automatically — no human needed to ask "why?".
    """
    ch = _clickhouse()
    ctx = load_run_context(ch, run_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"agent_run '{run_id}' not found")

    outcomes = _run_evaluations(ctx, expected)
    _persist_evals(ch, ctx.run, outcomes)
    eval_resp = _eval_response(run_id, outcomes)

    outcome_wrong = ctx.run.get("outcome_correct") == 0
    run_errored = ctx.run.get("status") == "error"
    failed_eval = not eval_resp.all_passed or eval_resp.overall_score < score_threshold
    should_rca = outcome_wrong or run_errored or failed_eval

    if not should_rca:
        return TriageResponse(
            run_id=run_id,
            evaluation=eval_resp,
            rca_triggered=False,
            reason="Run passed evaluations; no RCA needed.",
        )

    reasons = []
    if outcome_wrong:
        reasons.append("outcome marked incorrect")
    if run_errored:
        reasons.append("run status=error")
    if failed_eval:
        reasons.append(f"eval score {eval_resp.overall_score} below {score_threshold} or a check failed")
    rca = _analyze_ctx(ch, ctx, run_id, trigger="auto_eval_failure")
    return TriageResponse(
        run_id=run_id,
        evaluation=eval_resp,
        rca_triggered=True,
        rca=rca,
        reason="Auto-triggered RCA: " + "; ".join(reasons),
    )


# --- V3: Business Outcome Correlation --------------------------------------
class KpiPoint(BaseModel):
    metric: str
    value: float
    bucket_start: str
    unit: str = ""
    segment: str = "all"
    tenant_id: str = "default"


class FactorModel(BaseModel):
    factor: str
    weight: float
    evidence: str


class CorrelationResponse(BaseModel):
    metric: str
    segment: str
    window_start: str
    window_end: str
    baseline_value: float
    current_value: float
    delta_pct: float
    direction: str
    driver_metric: str
    correlation_score: float
    summary: str
    factors: list[FactorModel]


@app.post("/kpi")
def ingest_kpi(points: list[KpiPoint]) -> dict:
    """Ingest business KPI points (revenue, conversions, support resolution, ...)."""
    from uuid import uuid4

    ch = _clickhouse()
    rows = [
        {
            "schema_version": "1.0.0",
            "tenant_id": p.tenant_id,
            "kpi_id": str(uuid4()),
            "metric": p.metric,
            "unit": p.unit,
            "bucket_start": p.bucket_start,
            "value": p.value,
            "segment": p.segment,
        }
        for p in points
    ]
    ch.insert_json("business_kpi", rows)
    return {"ingested": len(rows)}


@app.post("/correlate", response_model=CorrelationResponse)
def run_correlation(
    metric: str, segment: str = "all", tenant_id: str = "default", persist: bool = True
) -> CorrelationResponse:
    """Correlate a KPI's latest shift with agent-runtime telemetry in the same window."""
    from uuid import uuid4

    ch = _clickhouse()
    result = correlate(ch, tenant_id, metric, segment)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"not enough KPI history for metric '{metric}' (need >= 2 buckets)",
        )
    if persist:
        row = result.to_row(tenant_id)
        row["correlation_id"] = str(uuid4())
        ch.insert_json("outcome_correlation", [row])

    return CorrelationResponse(
        metric=result.metric,
        segment=result.segment,
        window_start=result.window_start,
        window_end=result.window_end,
        baseline_value=result.baseline_value,
        current_value=result.current_value,
        delta_pct=round(result.delta_pct, 2),
        direction=result.direction,
        driver_metric=result.driver_metric,
        correlation_score=result.correlation_score,
        summary=result.summary,
        factors=[FactorModel(**f.__dict__) for f in result.factors],
    )
