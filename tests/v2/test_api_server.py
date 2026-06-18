"""v2 API 서버 테스트 (B2C 계약 v1.0 — docs/integration/pipeline-service-api-contract.md).

mock graph factory 주입으로 LLM/sandbox 없이 결정론 검증:
- 인증(X-API-Key) / healthz 무인증
- generate 202 + job 폴링 → success 패키지 형상 (계약 §2.5)
- fail_qa → 패키지+QA 리포트 동봉 (결정 ④) / 그 외 fail → package null + diagnostics
- 422(unknown seed) / 404(미지 job) / 429(슬롯 초과, Retry-After) / idempotency
- graph 예외 → status=failed (백엔드는 5xx 류로 재시도)
"""

from __future__ import annotations

import threading
import time
from typing import Any

from fastapi.testclient import TestClient

from ipe.v1.schema import (
    FailureMode,
    GeneratedTestCase,
    IOContract,
    IOFieldSpec,
    IOSchema,
    ProblemBlueprint,
    ProblemSpec,
    QAFinding,
    QAReport,
    QAReview,
    SampleResult,
    SampleTestCase,
    SolutionAttempt,
    TargetAlgorithm,
    TestSuite,
    VerificationResult,
)
from ipe.v2.api import _build_diagnostics, create_app
from ipe.v2.state import V2State, initial_v2_state

_KEY = "test-key"


# ---------- 상태 픽스처 ----------


def _blueprint() -> ProblemBlueprint:
    return ProblemBlueprint(
        reduction_core=TargetAlgorithm.DIJKSTRA,
        composition=(TargetAlgorithm.UNION_FIND,),
        domain="waterworks",
        io_schema=IOSchema(
            inputs=(IOFieldSpec(name="g", type="weighted_edges"),),
            output_type="int",
            output_format="단일 정수",
        ),
    )


def _spec() -> ProblemSpec:
    return ProblemSpec(
        target_algorithm=TargetAlgorithm.DIJKSTRA,
        title="상수도 배관망 점검",
        description="은닉 지문",
        io_contract=IOContract(input_format="V E\nu v w...", output_format="단일 정수"),
        sample_testcases=[
            SampleTestCase(input_text="1 0", expected_output="0"),
            SampleTestCase(input_text="2 1\n1 2 5\n1 2", expected_output="5"),
            SampleTestCase(input_text="3 2\n1 2 5\n2 3 7\n1 3", expected_output="12"),
        ],
    )


def _suite() -> TestSuite:
    return TestSuite(
        cases=(
            GeneratedTestCase(
                input_text="1 0",
                category="edge:empty",
                expected_output="0",
                golden_elapsed_ms=12,
            ),
            GeneratedTestCase(
                input_text="3 2\n1 2 5\n2 3 7",
                category="scale:small",
                expected_output="12",
                golden_elapsed_ms=180,
            ),
        ),
        golden_origin="claude-opus-4-7",
    )


def _attempt() -> SolutionAttempt:
    return SolutionAttempt(
        code="import sys\n\ndef main() -> None:\n    print(0)  # golden dijkstra\n",
        iteration=1,
    )


def _qa_pass() -> QAReport:
    return QAReport(
        reviews=tuple(
            QAReview(kind=k, passed=True)
            for k in ("ambiguity", "fairness", "leakage", "difficulty")
        )
    )


def _qa_fail() -> QAReport:
    reviews = [
        QAReview(kind=k, passed=True)
        for k in ("fairness", "leakage", "difficulty")
    ]
    reviews.append(
        QAReview(
            kind="ambiguity",
            passed=False,
            rationale="경계 미정의",
            findings=(QAFinding(severity="blocker", description="source==sink 모호"),),
        )
    )
    return QAReport(reviews=tuple(reviews))


def _final_state(final_status: str, *, qa: QAReport | None = None) -> V2State:
    base = initial_v2_state("job", TargetAlgorithm.DIJKSTRA)
    update: dict[str, object] = {"final_status": final_status, "qa_routebacks": 1}
    if final_status in ("success", "fail_qa"):
        update.update(
            {
                "blueprint": _blueprint(),
                "spec": _spec(),
                "attempt": _attempt(),
                "test_suite": _suite(),
                "qa_report": qa if qa is not None else _qa_pass(),
            }
        )
    if final_status == "fail_spec_authoring":
        update["spec_authoring_error"] = "KeyError: boom"
    return base.model_copy(update=update)


# ---------- mock graphs ----------


class _FakeGraph:
    def __init__(self, final: V2State) -> None:
        self._final = final

    def invoke(self, state: Any, config: Any = None) -> V2State:
        return self._final


class _BlockingGraph:
    """release 이벤트까지 invoke 가 블록 — 429 슬롯 테스트용."""

    def __init__(self, final: V2State) -> None:
        self.release = threading.Event()
        self._final = final

    def invoke(self, state: Any, config: Any = None) -> V2State:
        assert self.release.wait(timeout=5.0)
        return self._final


class _RaisingGraph:
    def invoke(self, state: Any, config: Any = None) -> V2State:
        msg = "anthropic auth error"
        raise RuntimeError(msg)


def _client(graph: Any, *, max_concurrent: int = 2) -> TestClient:
    app = create_app(
        graph_factory=lambda req: graph, api_key=_KEY, max_concurrent=max_concurrent
    )
    return TestClient(app)


def _generate(client: TestClient, *, key: str = _KEY, **body_over: Any) -> Any:
    body = {
        "mode": "p2",
        "seed_algorithm": "dijkstra",
        "idempotency_key": "idem-1",
    }
    body.update(body_over)
    return client.post(
        "/v1/problems/generate", json=body, headers={"X-API-Key": key}
    )


def _poll_completed(client: TestClient, job_id: str, timeout_s: float = 3.0) -> Any:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = client.get(f"/v1/jobs/{job_id}", headers={"X-API-Key": _KEY})
        assert r.status_code == 200, r.text
        data = r.json()
        if data["status"] != "running":
            return data
        time.sleep(0.02)
    msg = "job did not complete in time"
    raise AssertionError(msg)


# ---------- 인증/기본 ----------


def test_healthz_requires_no_auth() -> None:
    client = _client(_FakeGraph(_final_state("success")))
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_generate_rejects_bad_api_key() -> None:
    client = _client(_FakeGraph(_final_state("success")))
    assert _generate(client, key="wrong").status_code == 401
    r = client.post("/v1/problems/generate", json={})
    assert r.status_code == 401  # 헤더 자체 부재


def test_invalid_seed_algorithm_is_422() -> None:
    client = _client(_FakeGraph(_final_state("success")))
    r = _generate(client, seed_algorithm="greedy")
    assert r.status_code == 422


def test_unknown_job_is_404() -> None:
    client = _client(_FakeGraph(_final_state("success")))
    r = client.get("/v1/jobs/nope", headers={"X-API-Key": _KEY})
    assert r.status_code == 404


# ---------- 패키지 형상 (계약 §2.5) ----------


def test_success_package_shape() -> None:
    client = _client(_FakeGraph(_final_state("success")))
    r = _generate(client)
    assert r.status_code == 202
    data = _poll_completed(client, r.json()["job_id"])

    assert data["final_status"] == "success"
    pkg = data["package"]
    assert pkg["problem"]["title"] == "상수도 배관망 점검"
    assert pkg["problem"]["io_contract"]["output_format"] == "단일 정수"
    assert pkg["problem"]["sample_testcases"][0]["expected_output"] == "0"
    cases = pkg["test_suite"]["cases"]
    assert [c["golden_elapsed_ms"] for c in cases] == [12, 180]
    assert pkg["test_suite"]["origin"] == "claude-opus-4-7"
    sol = pkg["solution"]  # 정해코드 동봉 (내부 검수용·응시자 비노출)
    assert "golden dijkstra" in sol["golden_code"]
    assert sol["language"] == "python"
    meta = pkg["meta"]
    assert meta["package_version"] == "1.0"
    assert meta["mode"] == "p2"
    assert meta["hidden_algorithm"] == "dijkstra"
    assert meta["composition"] == ["union_find"]
    assert meta["golden_language"] == "python"
    assert meta["timing"]["max_golden_elapsed_ms"] == 180
    assert meta["qa"]["overall_pass"] is True
    assert meta["generation"]["qa_routebacks"] == 1


def test_fail_qa_returns_package_with_qa_findings() -> None:
    """결정 ④: fail_qa 는 문제+채점셋 완성 상태 — draft 검수용으로 패키지 반환."""
    client = _client(_FakeGraph(_final_state("fail_qa", qa=_qa_fail())))
    r = _generate(client)
    data = _poll_completed(client, r.json()["job_id"])

    assert data["final_status"] == "fail_qa"
    pkg = data["package"]
    assert pkg is not None
    qa = pkg["meta"]["qa"]
    assert qa["overall_pass"] is False
    assert qa["verdicts"]["ambiguity"] is False
    assert any(
        f["kind"] == "ambiguity" and f["severity"] == "blocker"
        for f in qa["findings"]
    )


def test_other_fail_has_no_package_but_diagnostics() -> None:
    client = _client(_FakeGraph(_final_state("fail_spec_authoring")))
    r = _generate(client)
    data = _poll_completed(client, r.json()["job_id"])

    assert data["final_status"] == "fail_spec_authoring"
    assert data["package"] is None
    assert "KeyError" in data["diagnostics"]["detail"]


def test_diagnostics_unpacks_verification_failure_evidence() -> None:
    """fail_verification 진단이 failure_mode + 실패 sample(expected/actual/stderr)
    + invariant + hint 를 푼다 — 19-batch 의 'overall_pass=False' 깜깜이 빈틈 대응."""
    v = VerificationResult(
        overall_pass=False,
        failure_mode=FailureMode.SAMPLE_MISMATCH,
        sample_results=[
            SampleResult(
                index=0,
                passed=True,
                expected_output="1",
                actual_output="1",
                elapsed_ms=5,
            ),
            SampleResult(
                index=1,
                passed=False,
                expected_output="42",
                actual_output="7",
                stderr="",
                elapsed_ms=12,
            ),
        ],
        iteration=1,
    )
    final = _final_state("fail_verification").model_copy(update={"verification": v})
    diag = _build_diagnostics(final)

    assert "sample_mismatch" in diag["detail"]
    assert "sample[1]" in diag["detail"]  # 실패 sample 만
    assert "sample[0]" not in diag["detail"]  # 통과 sample 은 생략
    assert "expected='42'" in diag["detail"]
    assert "actual='7'" in diag["detail"]


def test_diagnostics_verification_surfaces_stderr_for_crash() -> None:
    """sample_crash 의 멀티라인 트레이스백에서 예외 줄(마지막)을 추출 — File 프레임
    경로가 아닌 예외 타입이 진단 핵심 (배치 RTE 정체 가시화)."""
    tb = (
        "Traceback (most recent call last):\n"
        '  File "sol.py", line 5, in <module>\n'
        "    a, b = data[i], data[i + 1]\n"
        "IndexError: list index out of range"
    )
    v = VerificationResult(
        overall_pass=False,
        failure_mode=FailureMode.SAMPLE_CRASH,
        sample_results=[
            SampleResult(
                index=0,
                passed=False,
                expected_output="5",
                actual_output="",
                stderr=tb,
                elapsed_ms=8,
            ),
        ],
        iteration=1,
    )
    final = _final_state("fail_verification").model_copy(update={"verification": v})
    diag = _build_diagnostics(final)

    assert "sample_crash" in diag["detail"]
    assert "IndexError: list index out of range" in diag["detail"]
    assert "File" not in diag["detail"]  # 프레임 경로는 노이즈 — 추출 안 함


# ---------- 운영 규약 ----------


def test_idempotency_same_key_returns_same_job() -> None:
    client = _client(_FakeGraph(_final_state("success")))
    first = _generate(client, idempotency_key="same")
    second = _generate(client, idempotency_key="same")
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]


def test_429_when_slots_exhausted() -> None:
    blocking = _BlockingGraph(_final_state("success"))
    client = _client(blocking, max_concurrent=1)
    first = _generate(client, idempotency_key="a")
    assert first.status_code == 202
    second = _generate(client, idempotency_key="b")
    assert second.status_code == 429
    assert "Retry-After" in second.headers
    blocking.release.set()
    data = _poll_completed(client, first.json()["job_id"])
    assert data["final_status"] == "success"


def test_graph_exception_surfaces_as_failed_job() -> None:
    """가드 밖 예외(LLM 인증 오류 등) — 5xx 류로 재시도하라는 failed 상태."""
    client = _client(_RaisingGraph())
    r = _generate(client)
    data = _poll_completed(client, r.json()["job_id"])
    assert data["status"] == "failed"
    assert "RuntimeError" in data["error"]


# ---------- 난이도 calibration (RFC R4 — 기본 off, 주입 시 meta.difficulty) ----------


class _FakeDifficultyLLM:
    """주입형 mock — 고정 BOJ 티어 반환 (실 LLM/anchor 불요)."""

    def evaluate(self, package: Any, *, anchors: Any) -> Any:
        from ipe.v1.schema import DifficultyFactors, DifficultyReport

        return DifficultyReport(
            label="Gold IV",
            reasoning="bj_1753_gold5 와 동형 — 단일 출발점 다익스트라",
            factors=DifficultyFactors(
                algorithm="dijkstra", complexity="O((V+E) log V)", n_max=20000
            ),
            calibration_anchors=(),
        )


def test_difficulty_annotated_when_llm_injected() -> None:
    """difficulty_llm 주입 시 success 패키지 meta.difficulty 동봉 (티어 파생 포함)."""
    app = create_app(
        graph_factory=lambda req: _FakeGraph(_final_state("success")),
        api_key=_KEY,
        difficulty_llm=_FakeDifficultyLLM(),
    )
    client = TestClient(app)
    r = _generate(client)
    data = _poll_completed(client, r.json()["job_id"])
    diff = data["package"]["meta"]["difficulty"]
    assert diff["label"] == "Gold IV"
    assert diff["tier"] == "Gold"  # computed 파생


def test_difficulty_absent_by_default() -> None:
    """주입/ env 없으면 난이도 미주석 (기본 off — 라이브 경로 비용·지연 보호)."""
    client = _client(_FakeGraph(_final_state("success")))
    r = _generate(client)
    data = _poll_completed(client, r.json()["job_id"])
    assert "difficulty" not in data["package"]["meta"]
