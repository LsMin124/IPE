"""v2 API 서버 — 파이프라인↔서비스 백엔드 계약 v1.0 (Slice 1).

단일 진실원천: ``docs/integration/pipeline-service-api-contract.md``.
설계 원칙 (계약 §0): **stateless** — 문제 영속화는 서비스 백엔드 DB 가 전담,
이 서버는 진행 중 job 만 in-memory 로 유지한다 (재시작 유실 → 백엔드가 404 를
보고 새 generate 로 재시도).

실행::

    IPE_API_KEY=... uvicorn 'ipe.v2.api:create_app' --factory --port 8000

graph_factory 주입으로 테스트는 LLM/sandbox 없이 결정론 검증 (CLI main_v2 의
``graph=`` 주입과 동일 패턴).
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ipe.v1.schema import TargetAlgorithm, VerificationResult
from ipe.v1.verification._exec import exception_signal

from . import config
from .difficulty import AnthropicDifficultyLLM, DifficultyLLM, annotate_difficulty
from .main_v2 import _normalize_final_state
from .state import V2State, initial_v2_state

PACKAGE_VERSION = "1.0"
GOLDEN_LANGUAGE = "python"
DEFAULT_MAX_CONCURRENT = 2
RETRY_AFTER_SECONDS = 60

# e2e 실증값 (tests/v2/test_e2e_v2_qa_real_llm.py) — faithfulness regen +
# QA back-route revise 사이클을 감당하는 바운드. 값은 config 단일 소스.
_MAX_ITERATIONS = config.MAX_ITERATIONS_API
_RECURSION_LIMIT = config.RECURSION_LIMIT_API

_GOLDEN_MODELS = list(config.GOLDEN_MODELS)
_BRUTE_MODEL = config.BRUTE_MODEL

# 계약 §2.3: 패키지(문제+채점셋)가 완성된 terminal — fail_qa 는 검수 구제 대상.
_PACKAGED_STATUSES = ("success", "fail_qa")


class GenerateRequest(BaseModel):
    """계약 §2.1 요청 본문.

    ``mode`` (Phase 4 — P1/P2 수렴): ``"p1"``=단일·공개·QA 3종 / ``"p2"``=합성·은닉·
    QA 4종. 모드 노브(hidden/composition_mode/qa_kinds)는 ``config.mode_knobs`` 가 결정.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Literal["p1", "p2"]
    seed_algorithm: TargetAlgorithm
    with_qa: bool = True
    max_qa_routebacks: int = Field(default=1, ge=0)
    idempotency_key: str = Field(..., min_length=1)


@dataclass
class _Job:
    """in-memory job — stateless 원칙상 프로세스 생애만 산다."""

    job_id: str
    mode: str
    status: Literal["running", "completed", "failed"] = "running"
    final: V2State | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.monotonic)
    elapsed_s: float | None = None
    # 난이도 주석된 패키지 캐시 — _run_job 에서 1회 계산(get_job 폴링마다 재계산 방지).
    package: dict[str, Any] | None = None


def _production_graph_factory(req: GenerateRequest) -> Any:
    """CLI 와 동일 모델 구성의 full 그래프. 모드 노브(hidden/composition/qa_kinds)는
    ``config.mode_knobs`` (P1=단일·공개·QA3 / P2=합성·은닉·QA4)."""
    from ipe.v1.nodes import AnthropicCoderLLM

    from .graph import build_v2_graph

    hidden, composition_mode, qa_kinds = config.mode_knobs(req.mode)
    return build_v2_graph(
        hidden=hidden,
        composition_mode=composition_mode,
        golden_llms=[
            AnthropicCoderLLM(m, parse_discipline=True) for m in _GOLDEN_MODELS
        ],
        brute_llm=AnthropicCoderLLM(_BRUTE_MODEL, parse_discipline=True),
        golden_origins=_GOLDEN_MODELS,
        with_test_suite=True,
        with_qa=req.with_qa,
        qa_kinds=qa_kinds,
    )


# ---------- 패키지 직렬화 (계약 §2.5 — 필드명은 계약이 SSOT) ----------


def _build_package(
    final: V2State, *, mode: str, elapsed_s: float | None
) -> dict[str, Any] | None:
    spec = final.spec
    suite = final.test_suite
    if final.final_status not in _PACKAGED_STATUSES or spec is None or suite is None:
        return None
    bp = final.blueprint
    qa = final.qa_report
    elapsed_values = [
        c.golden_elapsed_ms for c in suite.cases if c.golden_elapsed_ms is not None
    ]
    qa_block: dict[str, Any] | None = None
    if qa is not None:
        qa_block = {
            "overall_pass": qa.overall_pass,
            "verdicts": {r.kind: r.passed for r in qa.reviews},
            "findings": [
                {
                    "kind": r.kind,
                    "severity": f.severity,
                    "description": f.description,
                }
                for r in qa.reviews
                for f in r.findings
            ],
        }
    # solution.golden_code — 내부 검수용 정해 (응시자 비노출, meta.hidden_algorithm 과
    # 동급 internal-only). reconciled canonical(suite expected 를 채운 검증된 골든)이라
    # packaged 상태면 attempt 는 항상 set; 이론상 부재는 None 으로 안전 처리.
    attempt = final.attempt
    solution_block: dict[str, Any] | None = None
    if attempt is not None:
        solution_block = {
            "golden_code": attempt.code,
            "language": attempt.language,
        }
    return {
        "problem": {
            "title": spec.title,
            "description": spec.description,
            "io_contract": spec.io_contract.model_dump(),
            "constraints": [c.model_dump() for c in spec.constraints],
            "sample_testcases": [s.model_dump() for s in spec.sample_testcases],
        },
        "solution": solution_block,
        "test_suite": {
            "cases": [
                {
                    "input_text": c.input_text,
                    "expected_output": c.expected_output,
                    "category": c.category,
                    "golden_elapsed_ms": c.golden_elapsed_ms,
                }
                for c in suite.cases
            ],
            "origin": suite.golden_origin,
        },
        "meta": {
            "package_version": PACKAGE_VERSION,
            "mode": mode,
            "hidden_algorithm": spec.target_algorithm.value,
            "composition": [a.value for a in bp.composition] if bp is not None else [],
            "domain": bp.domain if bp is not None else "",
            "golden_language": GOLDEN_LANGUAGE,
            "qa": qa_block,
            "verification": (
                {"overall_pass": final.verification.overall_pass}
                if final.verification is not None
                else None
            ),
            "timing": {
                "max_golden_elapsed_ms": max(elapsed_values) if elapsed_values else None
            },
            "generation": {
                "elapsed_s": elapsed_s,
                "iteration": final.iteration,
                "qa_routebacks": final.qa_routebacks,
            },
        },
    }


# verification 진단 상세 바운드 — 실패 sample/invariant 증거와 문자열 폭주 사이 균형.
_DIAG_MAX_ITEMS = 3
_DIAG_HEAD = 80


def _diag_head(text: str, limit: int = _DIAG_HEAD) -> str:
    """한 줄 진단용 head — 공백/개행 접어 truncate (reconcile._head 와 동일 규약)."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _verification_detail(v: VerificationResult) -> list[str]:
    """fail_verification 의 증거를 푼다 — failure_mode + 실패 sample(expected/actual/
    stderr/elapsed) + invariant + hint. 기존엔 'overall_pass=False' 한 줄만 담아
    19-algo 배치의 verification 버킷(20건)이 깜깜이였던 빈틈 대응."""
    lines = [f"verification: failure_mode={v.failure_mode.value}"]
    failed = [s for s in v.sample_results if not s.passed]
    for s in failed[:_DIAG_MAX_ITEMS]:
        lines.append(
            f"  sample[{s.index}]: expected={_diag_head(s.expected_output)!r} "
            f"actual={_diag_head(s.actual_output)!r} "
            f"stderr={exception_signal(s.stderr, _DIAG_HEAD)!r} ({s.elapsed_ms}ms)"
        )
    for iv in v.invariant_violations[:_DIAG_MAX_ITEMS]:
        lines.append(f"  invariant[{iv.invariant_kind}]: {_diag_head(iv.description)}")
    if v.feedback is not None:
        lines.append(f"  hint: {_diag_head(v.feedback.actionable_hint)}")
    return lines


def _build_diagnostics(final: V2State) -> dict[str, Any]:
    detail: list[str] = []
    if final.spec_authoring_error is not None:
        detail.append(f"spec_authoring_error: {final.spec_authoring_error}")
    rec = final.reconciliation
    if rec is not None and not rec.all_agree:
        detail.extend(f"reconcile: {d}" for d in rec.disagreements)
    v = final.verification
    if v is not None and not v.overall_pass:
        detail.extend(_verification_detail(v))
    return {"summary": str(final.final_status), "detail": "\n".join(detail)}


# ---------- app ----------


def create_app(
    *,
    graph_factory: Any = None,
    api_key: str | None = None,
    max_concurrent: int | None = None,
    difficulty_llm: DifficultyLLM | None = None,
) -> FastAPI:
    """API app 팩토리 — production 은 인자 없이 (env 기반), 테스트는 주입.

    - ``IPE_API_KEY`` (필수): 정적 인증 키 (계약 §1).
    - ``IPE_MAX_CONCURRENT_GENERATIONS`` (기본 2): 동시 생성 슬롯 (비용·sandbox 바운드).
    """
    resolved_key = api_key if api_key is not None else os.environ.get("IPE_API_KEY")
    if not resolved_key:
        msg = "IPE_API_KEY 가 없습니다 — 인증 없는 생성 API 는 금지 (비용 보호)"
        raise RuntimeError(msg)
    resolved_factory = (
        graph_factory if graph_factory is not None else _production_graph_factory
    )
    # 난이도 calibration(RFC R4) — 기본 off. 주입(테스트) > ``IPE_WITH_DIFFICULTY`` env
    # (production opt-in) > None. 켜지면 _run_job 이 패키지에 meta.difficulty 를 주석한다.
    resolved_difficulty: DifficultyLLM | None
    if difficulty_llm is not None:
        resolved_difficulty = difficulty_llm
    elif os.environ.get("IPE_WITH_DIFFICULTY"):
        resolved_difficulty = AnthropicDifficultyLLM()
    else:
        resolved_difficulty = None
    slots = threading.BoundedSemaphore(
        max_concurrent
        if max_concurrent is not None
        else int(
            os.environ.get("IPE_MAX_CONCURRENT_GENERATIONS", DEFAULT_MAX_CONCURRENT)
        )
    )

    app = FastAPI(title="IPE pipeline API", version=PACKAGE_VERSION)
    jobs: dict[str, _Job] = {}
    idempotency: dict[str, str] = {}
    lock = threading.Lock()

    @app.middleware("http")
    async def _require_api_key(request: Request, call_next: Any) -> Any:
        # 미들웨어 레벨 인증 — body 검증(422)보다 먼저 401 (비용 보호가 1순위 게이트).
        if request.url.path.startswith("/v1/") and (
            request.headers.get("X-API-Key") != resolved_key
        ):
            return JSONResponse(status_code=401, content={"detail": "invalid api key"})
        return await call_next(request)

    def _run_job(job: _Job, req: GenerateRequest) -> None:
        try:
            graph = resolved_factory(req)
            initial = initial_v2_state(
                job.job_id,
                req.seed_algorithm,
                max_iterations=_MAX_ITERATIONS,
                max_qa_routebacks=req.max_qa_routebacks,
            )
            raw = graph.invoke(initial, config={"recursion_limit": _RECURSION_LIMIT})
            final = _normalize_final_state(raw)
            elapsed_s = round(time.monotonic() - job.started_at, 1)
            # 난이도 주석(RFC R4) — 켜진 경우만, 락 밖에서 1회 계산(LLM 호출). 실패해도
            # 패키지 출하는 막지 않는다(난이도는 주석, 게이트 아님).
            annotated: dict[str, Any] | None = None
            if resolved_difficulty is not None:
                pkg = _build_package(final, mode=job.mode, elapsed_s=elapsed_s)
                if pkg is not None:
                    try:
                        annotated = annotate_difficulty(
                            pkg, llm=resolved_difficulty
                        )
                    except Exception:  # noqa: BLE001 — 주석 실패는 무시(패키지 보존)
                        annotated = None
            with lock:
                job.final = final
                job.status = "completed"
                job.elapsed_s = elapsed_s
                job.package = annotated
        except Exception as exc:  # noqa: BLE001 — job 격리 (failed = 백엔드 재시도 신호)
            with lock:
                job.error = f"{type(exc).__name__}: {exc}"[:500]
                job.status = "failed"
                job.elapsed_s = round(time.monotonic() - job.started_at, 1)
        finally:
            slots.release()

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/problems/generate", status_code=202)
    def generate(
        req: GenerateRequest, request: Request, response: Response
    ) -> dict[str, str]:
        with lock:
            existing = idempotency.get(req.idempotency_key)
            if existing is not None:
                return {"job_id": existing}
        if not slots.acquire(blocking=False):
            raise HTTPException(
                status_code=429,
                detail="generation slots exhausted",
                headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
            )
        job = _Job(job_id=uuid.uuid4().hex, mode=req.mode)
        with lock:
            jobs[job.job_id] = job
            idempotency[req.idempotency_key] = job.job_id
        threading.Thread(target=_run_job, args=(job, req), daemon=True).start()
        return {"job_id": job.job_id}

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str, request: Request) -> dict[str, Any]:
        with lock:
            job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="unknown job")
        if job.status == "running":
            return {"status": "running"}
        if job.status == "failed":
            return {"status": "failed", "error": job.error}
        final = job.final
        assert final is not None  # completed 이면 항상 set (불변식)
        # 난이도 주석된 패키지가 캐시돼 있으면 재사용(폴링마다 재계산·재호출 방지).
        package = (
            job.package
            if job.package is not None
            else _build_package(final, mode=job.mode, elapsed_s=job.elapsed_s)
        )
        body: dict[str, Any] = {
            "status": "completed",
            "final_status": final.final_status,
            "package": package,
        }
        if package is None:
            body["diagnostics"] = _build_diagnostics(final)
        return body

    return app
