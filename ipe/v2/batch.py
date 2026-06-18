"""v2 배치 검증/문제 은행 적재 CLI — 시드 전수 × N run 풀 스윕.

백엔드 전달 전 19-algo 전수 검증(시드별 출하율 측정)과 문제 은행 선적재를 한
실행으로 겸한다. run 산출 파일은 계약 §2.5 패키지(= API ``GET /v1/jobs`` 응답과
동일 형상, ``api._build_package`` 재사용) + batch 메타라서 서비스 백엔드가 그대로
임포트할 수 있다. 그래프 구성도 ``api._production_graph_factory`` 재사용 — API
서버와 배치가 항상 같은 production 경로를 측정한다.

Usage::

    python -m ipe.v2.batch --seeds all --runs-per-seed 3 --concurrency 2
    python -m ipe.v2.batch --seeds dijkstra,bfs --runs-per-seed 1 --dry-run
    python -m ipe.v2.batch --report-only

env: ``ANTHROPIC_API_KEY`` (production 실행 시 필수 — 시작 시 fail-fast).
산출 디렉토리 기본값 ``outputs/bank`` 는 gitignored. 재실행하면 기존 run 파일은
skip(resume) — ``--retry-failed`` 면 crash(status=failed) 분만 재시도. 각 run 의
graph 예외는 격리되어 ``status=failed`` 로 기록되고 배치는 계속된다.
exit code: crash 존재 시 1 (``fail_*`` 종료는 정상 측정값이라 0).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from dotenv import load_dotenv
from langchain_core.callbacks import get_usage_metadata_callback
from sqlalchemy.engine import Engine

from ipe.v1.schema import TargetAlgorithm
from ipe.v2.db import init_schema, persist_run

from . import config
from .api import (
    _MAX_ITERATIONS,
    _RECURSION_LIMIT,
    GenerateRequest,
    _build_diagnostics,
    _build_package,
    _production_graph_factory,
)
from .difficulty import AnthropicDifficultyLLM, annotate_difficulty
from .main_v2 import _normalize_final_state
from .state import initial_v2_state

# 공식 단가 per MTok (input, output) — config 단일 소스(계약 §5, 5fb370f).
_PRICING_PER_MTOK = config.PRICING_PER_MTOK
# 실측 N=2 어림 (계약 §5) — 계획 출력용 추정치.
_PER_RUN_COST_ESTIMATE = "$0.4~0.6"

_Mode = Literal["p1", "p2"]  # Phase 4 — P1/P2 생성 파이프라인 모드


@dataclass(frozen=True)
class _RunTask:
    seed: TargetAlgorithm
    run_index: int  # 1-based
    path: Path
    run_id: str


def _apply_memory_limit(gb: int) -> None:
    """single-run 자식의 자기 메모리 제한 (best-effort).

    실측: 일부 run 의 입력 생성이 메모리 폭주 → OS SIGKILL(rc=-9) 로 증거·usage
    까지 소실 + 12~27분 슬롯 점유 + 스왑으로 머신 전체 저하. 자기제한을 걸면
    수 분 내 MemoryError 로 전환 → 격리 핸들러가 failed 로 기록(빠른 실패+증거).
    macOS 는 RLIMIT_AS 를 안 지키는 경우가 있어 DATA 도 함께 건다. fork-안전성
    문제(preexec_fn+threads) 회피를 위해 부모가 아닌 자식이 스스로 건다.
    """
    if gb <= 0:
        return
    import resource

    limit = gb * 1024**3
    for name in ("RLIMIT_DATA", "RLIMIT_AS"):
        try:
            resource.setrlimit(getattr(resource, name), (limit, limit))
        except (ValueError, OSError):  # pragma: no cover — OS 별 미지원 무해
            continue


def _parse_seeds(value: str) -> list[TargetAlgorithm]:
    """``--seeds`` 파싱 — 'all'=전체 enum, 그 외 comma-sep enum 값."""
    if value.strip().lower() == "all":
        return list(TargetAlgorithm)
    seeds: list[TargetAlgorithm] = []
    for token in value.split(","):
        name = token.strip().lower()
        if not name:
            continue
        try:
            seeds.append(TargetAlgorithm(name))
        except ValueError as e:
            valid = [a.value for a in TargetAlgorithm]
            msg = f"unsupported seed '{name}'. supported: all | {valid}"
            raise SystemExit(msg) from e
    if not seeds:
        raise SystemExit("--seeds 는 최소 1개 seed 필요")
    return seeds


def _cost_usd(usage: Mapping[str, Any]) -> float:
    """모델별 토큰 사용량 → USD. 단가 미상 모델은 집계 제외 — 오표기 방지 우선
    (미상 모델은 run 파일의 usage 원본으로 추적 가능)."""
    total = 0.0
    for model, u in usage.items():
        for key, (price_in, price_out) in _PRICING_PER_MTOK.items():
            if key in model:
                total += (
                    u.get("input_tokens", 0) / 1e6 * price_in
                    + u.get("output_tokens", 0) / 1e6 * price_out
                )
                break
    return round(total, 4)


def _existing_status(path: Path) -> str | None:
    """기존 run 파일의 status — 없거나 손상이면 None(=실행 대상)."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    status = data.get("status")
    return status if isinstance(status, str) else None


def _plan_runs(
    seeds: Sequence[TargetAlgorithm],
    runs_per_seed: int,
    out_dir: Path,
    *,
    retry_failed: bool,
) -> list[_RunTask]:
    """실행 대상 (seed, run_index) 계획 — completed 는 항상 skip(resume),
    failed 는 ``--retry-failed`` 일 때만 재실행."""
    tasks: list[_RunTask] = []
    for seed in seeds:
        for idx in range(1, runs_per_seed + 1):
            path = out_dir / f"{seed.value}_run{idx}.json"
            status = _existing_status(path)
            if status == "completed" or (status == "failed" and not retry_failed):
                continue
            run_id = f"bank-{seed.value}-r{idx}-{uuid.uuid4().hex[:6]}"
            tasks.append(_RunTask(seed=seed, run_index=idx, path=path, run_id=run_id))
    return tasks


def _execute_run(
    task: _RunTask,
    graph_factory: Any,
    *,
    mode: _Mode,
    max_qa_routebacks: int,
) -> dict[str, Any]:
    """단일 run — 예외는 격리해 status=failed 로 기록 (api._run_job 과 동일 규약).

    usage 집계는 context-var callback — run 마다 worker 스레드 안에서 진입하므로
    동시 run 간 격리된다 (langgraph 병렬 브랜치는 contextvars copy 로 전파).
    """
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    t0 = time.monotonic()
    body: dict[str, Any]
    try:
        req = GenerateRequest(
            mode=mode,
            seed_algorithm=task.seed,
            max_qa_routebacks=max_qa_routebacks,
            idempotency_key=task.run_id,
        )
        graph = graph_factory(req)
        initial = initial_v2_state(
            task.run_id,
            task.seed,
            max_iterations=_MAX_ITERATIONS,
            max_qa_routebacks=max_qa_routebacks,
        )
        with get_usage_metadata_callback() as cb:
            raw = graph.invoke(initial, config={"recursion_limit": _RECURSION_LIMIT})
        final = _normalize_final_state(raw)
        elapsed_s = round(time.monotonic() - t0, 1)
        usage = {model: dict(u) for model, u in cb.usage_metadata.items()}
        package = _build_package(final, mode=mode, elapsed_s=elapsed_s)
        body = {
            "status": "completed",
            "final_status": final.final_status,
            "package": package,
            "composition": (
                [a.value for a in final.blueprint.composition]
                if final.blueprint is not None
                else None
            ),
            "usage": usage,
            "cost_usd": _cost_usd(usage),
        }
        if package is None:
            body["diagnostics"] = _build_diagnostics(final)
    except Exception as exc:  # noqa: BLE001 — run 격리 (crash 도 배치 데이터)
        elapsed_s = round(time.monotonic() - t0, 1)
        body = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:500]}
    body["batch"] = {
        "seed": task.seed.value,
        "run_index": task.run_index,
        "run_id": task.run_id,
        "mode": mode,
        "started_at": started_at,
        "elapsed_s": elapsed_s,
    }
    return body


def _write_json(path: Path, body: Mapping[str, Any]) -> None:
    """원자적 write (tmp+rename) — 배치 중단 시 부분 파일이 resume 을 오염 못 하게."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2, default=str))
    os.replace(tmp, path)


def _execute_run_subprocess(
    task: _RunTask,
    *,
    mode: _Mode,
    max_qa_routebacks: int,
    timeout_s: int,
    mem_limit_gb: int,
) -> dict[str, Any]:
    """production run 을 ``--single-run`` 자식 프로세스로 격리 실행.

    근거(실측): 일부 run 이 입력 생성 단계에서 CPU/메모리 폭주(28분+ 100% CPU)
    → 호스트가 프로세스를 kill 하면 in-flight 전체가 유실됐다. 격리하면 ①폭주
    run 만 timeout kill(=failed 데이터로 수렴) ②메모리 폭주가 배치 본체를 못
    죽임 ③GIL 밖이라 CPU 구간 진짜 병렬. 자식이 결과 파일을 쓰고, 부모는 그
    파일을 읽는다 — 파일 부재/타임아웃이면 부모가 failed 를 합성해 쓴다.
    """
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    t0 = time.monotonic()
    # retry 시 기존 failed 파일이 남아 있으면 자식 사망 후 stale 을 신선한
    # 결과로 오인한다(lis r3 실측: 이전 timestamp/elapsed 그대로 반환) — 선삭제로
    # '존재하는 파일 = 이번 자식이 쓴 것' 불변식 확보.
    task.path.unlink(missing_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "ipe.v2.batch",
        "--single-run",
        task.seed.value,
        "--run-index",
        str(task.run_index),
        "--run-id",
        task.run_id,
        "--out",
        str(task.path.parent),
        "--mode",
        mode,
        "--max-qa-routebacks",
        str(max_qa_routebacks),
        "--mem-limit-gb",
        str(mem_limit_gb),
    ]
    error: str
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired:
        # 완료 직전 race: 자식이 파일을 쓴 뒤 exit 전에 데드라인이 올 수 있다.
        result = _read_result(task.path)
        if result is not None:
            return result
        error = f"run timeout {timeout_s}s — subprocess killed (생성/실행 폭주 의심)"
    else:
        result = _read_result(task.path)
        if result is not None:
            return result
        tail = (proc.stderr or "").strip()[-300:]
        error = f"subprocess no result file (rc={proc.returncode}, stderr: {tail})"
    body: dict[str, Any] = {
        "status": "failed",
        "error": error[:500],
        "batch": {
            "seed": task.seed.value,
            "run_index": task.run_index,
            "run_id": task.run_id,
            "mode": mode,
            "started_at": started_at,
            "elapsed_s": round(time.monotonic() - t0, 1),
        },
    }
    _write_json(task.path, body)
    return body


def _read_result(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _execute_and_persist(
    task: _RunTask,
    graph_factory: Any,
    *,
    mode: _Mode,
    max_qa_routebacks: int,
    timeout_s: int,
    mem_limit_gb: int,
) -> dict[str, Any]:
    """run 실행+영속화 — 주입 factory(테스트)는 in-thread, production 은 subprocess 격리."""
    if graph_factory is not None:
        body = _execute_run(
            task, graph_factory, mode=mode, max_qa_routebacks=max_qa_routebacks
        )
        _write_json(task.path, body)
        return body
    return _execute_run_subprocess(
        task,
        mode=mode,
        max_qa_routebacks=max_qa_routebacks,
        timeout_s=timeout_s,
        mem_limit_gb=mem_limit_gb,
    )


# ---------- 집계/리포트 ----------


def _aggregate(out_dir: Path) -> dict[str, Any]:
    """run 파일 전체를 시드별로 집계 — 재실행해도 동일 결과 (파일이 SSOT)."""
    seeds: dict[str, dict[str, Any]] = {}
    for path in sorted(out_dir.glob("*_run*.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        seed = str(data.get("batch", {}).get("seed") or path.name.split("_run")[0])
        agg = seeds.setdefault(
            seed,
            {
                "runs": 0,
                "crashed": 0,
                "success": 0,
                "packaged": 0,
                "statuses": Counter(),
                "qa_failed_kinds": Counter(),
                "elapsed": [],
                "cost_usd": 0.0,
                "compositions": set(),
            },
        )
        agg["runs"] += 1
        elapsed = data.get("batch", {}).get("elapsed_s")
        if isinstance(elapsed, int | float):
            agg["elapsed"].append(float(elapsed))
        if data.get("status") == "failed":
            agg["crashed"] += 1
            continue
        final_status = str(data.get("final_status"))
        agg["statuses"][final_status] += 1
        agg["success"] += final_status == "success"
        cost = data.get("cost_usd")
        if isinstance(cost, int | float):
            agg["cost_usd"] += float(cost)
        comp = data.get("composition")
        if isinstance(comp, list):
            agg["compositions"].add(tuple(str(c) for c in comp))
        pkg = data.get("package")
        if pkg is not None:
            agg["packaged"] += 1
            if final_status == "fail_qa":
                qa = (pkg.get("meta") or {}).get("qa") or {}
                for kind, ok in (qa.get("verdicts") or {}).items():
                    if ok is False:
                        agg["qa_failed_kinds"][str(kind)] += 1

    seeds_out: dict[str, Any] = {}
    overall: dict[str, Any] = {
        "runs": 0,
        "crashed": 0,
        "success": 0,
        "packaged": 0,
        "cost_usd": 0.0,
    }
    for seed, agg in sorted(seeds.items()):
        elapsed_list: list[float] = agg["elapsed"]
        seeds_out[seed] = {
            "runs": agg["runs"],
            "crashed": agg["crashed"],
            "success": agg["success"],
            "packaged": agg["packaged"],
            "statuses": dict(agg["statuses"]),
            "qa_failed_kinds": dict(agg["qa_failed_kinds"]),
            "avg_elapsed_s": (
                round(sum(elapsed_list) / len(elapsed_list), 1) if elapsed_list else None
            ),
            "max_elapsed_s": max(elapsed_list) if elapsed_list else None,
            "cost_usd": round(agg["cost_usd"], 4),
            "compositions": sorted(list(c) for c in agg["compositions"]),
        }
        for key in ("runs", "crashed", "success", "packaged", "cost_usd"):
            overall[key] += agg[key]
    overall["cost_usd"] = round(overall["cost_usd"], 4)
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "seeds": seeds_out,
        "overall": overall,
    }


def _print_report(summary: Mapping[str, Any]) -> None:
    print("[report] === 시드별 배치 결과 (출하율 검증) ===")
    for seed, s in summary["seeds"].items():
        comps = ["+".join(c) for c in s["compositions"]]
        print(
            f"[report] {seed}: runs={s['runs']} crash={s['crashed']} "
            f"success={s['success']} packaged={s['packaged']} "
            f"statuses={s['statuses']} qa_failed={s['qa_failed_kinds']} "
            f"avg={s['avg_elapsed_s']}s max={s['max_elapsed_s']}s "
            f"cost=${s['cost_usd']} comps={comps}"
        )
    o = summary["overall"]
    print(
        f"[report] TOTAL: runs={o['runs']} crash={o['crashed']} "
        f"success={o['success']} packaged={o['packaged']} cost=${o['cost_usd']}"
    )


# ---------- CLI ----------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ipe.v2.batch",
        description="IPE v2 배치 검증/문제 은행 적재 — 시드 전수 × N run 풀 스윕",
    )
    parser.add_argument(
        "--seeds",
        default="all",
        help="'all'(전체 enum) 또는 comma-sep seed (예: dijkstra,bfs)",
    )
    parser.add_argument(
        "--runs-per-seed", type=int, default=3, help="시드당 run 수 (default: 3)"
    )
    parser.add_argument(
        "--out",
        default="outputs/bank",
        help="산출 디렉토리 (gitignored, default: outputs/bank)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=2, help="동시 run 수 (default: 2)"
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=10.0,
        help=(
            "누적 비용 상한 USD — 도달 시 미시작 run 중단 (default: 10, 0=무제한). "
            "큰 전수 배치는 명시적으로 올릴 것 (전수 ~$28)"
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["p1", "p2"],
        default="p2",
        help="생성 모드 (p1=단일·공개·QA3 / p2=합성·은닉·QA4, default: p2)",
    )
    parser.add_argument(
        "--max-qa-routebacks",
        type=int,
        default=1,
        help="QA back-route 예산 (default: 1)",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="기존 crash(status=failed) run 을 재시도 (completed 는 항상 skip)",
    )
    parser.add_argument(
        "--run-timeout",
        type=int,
        default=2400,
        help="run 별 subprocess 타임아웃 초 (default: 2400 — 정상 최대 ~1700s 관측)",
    )
    parser.add_argument(
        "--single-run",
        default=None,
        metavar="SEED",
        help="(internal) 단일 run 자식 모드 — 부모 배치가 격리 실행용으로 스폰",
    )
    parser.add_argument(
        "--mem-limit-gb",
        type=int,
        default=4,
        help="single-run 자식 자기 메모리 제한 GiB, 0=무제한 (default: 4)",
    )
    parser.add_argument(
        "--run-index", type=int, default=1, help="(internal) --single-run 의 인덱스"
    )
    parser.add_argument(
        "--run-id", default=None, help="(internal) --single-run 의 run_id"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="실행 없이 계획만 출력"
    )
    parser.add_argument(
        "--report-only", action="store_true", help="기존 산출물 집계/리포트만"
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help=(
            "공유 DB(PostgreSQL) 적재 URL — 지정 시 완료 run 마다 패키지를 DB 에 write "
            "(JSON 파일과 병행). 예: postgresql+psycopg://user:pw@host:5432/bank"
        ),
    )
    parser.add_argument(
        "--with-difficulty",
        action="store_true",
        help=(
            "적재 직전 BOJ 티어 난이도(RFC R4)를 calibration 해 meta.difficulty + "
            "problems.difficulty 컬럼에 주석 (--db-url 적재 시에만 적용, 패키지당 1 LLM콜)"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None, *, graph_factory: Any = None) -> int:
    """배치 entrypoint. ``graph_factory`` 주입 시 production 그래프 build 생략(테스트)."""
    load_dotenv()
    args = _build_parser().parse_args(argv)
    out_dir = Path(args.out)

    if args.report_only:
        summary = _aggregate(out_dir)
        _write_json(out_dir / "summary.json", summary)
        _print_report(summary)
        return 0

    if args.single_run is not None:
        # 내부 자식 모드 — 부모가 timeout/메모리 폭주를 프로세스 단위로 격리.
        seed = _parse_seeds(args.single_run)[0]
        out_dir.mkdir(parents=True, exist_ok=True)
        task = _RunTask(
            seed=seed,
            run_index=args.run_index,
            path=out_dir / f"{seed.value}_run{args.run_index}.json",
            run_id=args.run_id
            or f"bank-{seed.value}-r{args.run_index}-{uuid.uuid4().hex[:6]}",
        )
        if graph_factory is None:
            # production 자식만 자기제한 — 주입 테스트 경로는 호스트 프로세스라 금지.
            _apply_memory_limit(args.mem_limit_gb)
        factory = (
            graph_factory if graph_factory is not None else _production_graph_factory
        )
        body = _execute_run(
            task,
            factory,
            mode=cast(_Mode, args.mode),
            max_qa_routebacks=args.max_qa_routebacks,
        )
        _write_json(task.path, body)
        return 0 if body["status"] == "completed" else 1

    seeds = _parse_seeds(args.seeds)
    tasks = _plan_runs(
        seeds, args.runs_per_seed, out_dir, retry_failed=args.retry_failed
    )
    total = len(seeds) * args.runs_per_seed
    budget = args.max_cost_usd
    est_high = len(tasks) * 0.6  # run 당 상한 어림 (계약 §5 실측 $0.4~0.6)
    print(
        f"[batch] plan: seeds={len(seeds)} × N={args.runs_per_seed} = {total} run "
        f"(skip={total - len(tasks)}, 실행 대상={len(tasks)}, "
        f"동시 {args.concurrency}, 예상 {len(tasks)} × {_PER_RUN_COST_ESTIMATE})"
    )
    if budget:
        warn = (
            f" ⚠ 예상 최대 ${est_high:.0f} > 상한 — 일부만 실행 후 중단됨"
            if est_high > budget
            else ""
        )
        print(f"[batch] 비용 상한 ${budget:g}{warn} (0=무제한, --max-cost-usd 로 조정)")
    if args.dry_run:
        for t in tasks:
            print(f"[batch] planned: {t.seed.value} r{t.run_index} → {t.path}")
        return 0
    if tasks and graph_factory is None and not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY 가 없습니다 (.env) — production 배치 불가")
    mode = cast(_Mode, args.mode)

    db_engine: Engine | None = None
    if args.db_url:
        from sqlalchemy import create_engine

        db_engine = create_engine(args.db_url)
        init_schema(db_engine)
        # 자격증명 비노출 — '@' 뒤 host/db 만 로그.
        print(f"[batch] DB 적재 활성화 → {args.db_url.rsplit('@', 1)[-1]}", flush=True)

    # 난이도 calibration(RFC R4) — --with-difficulty 시에만. 적재 직전 주석(아래 persist).
    difficulty_llm = AnthropicDifficultyLLM() if args.with_difficulty else None
    if difficulty_llm is not None:
        print("[batch] 난이도 calibration 활성화 (적재 패키지에 meta.difficulty 주석)", flush=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    crashed = 0
    done = 0
    spent = 0.0  # 누적 비용 — worker 가 락 하에 갱신, guard 가 읽음
    spent_lock = threading.Lock()
    budget_stopped = False

    def _run_guarded(task: _RunTask) -> dict[str, Any] | None:
        """상한 도달 시 실행 자체를 skip → None 반환.

        사후 ``future.cancel()`` 만으론 빠른 run 에서 워커가 메인 루프의 취소 결정보다
        앞서 큐 태스크를 집어 상한이 샌다(고속 mock 에서 flaky·실 cap 누수 가능). 워커
        시작 시점에 공유 누적비용을 락으로 확인해 **타이밍 무관 결정론적**으로 차단한다.
        """
        nonlocal spent
        with spent_lock:
            if budget and spent >= budget:
                return None  # 상한 도달 — 미실행 skip
        body = _execute_and_persist(
            task,
            graph_factory,
            mode=mode,
            max_qa_routebacks=args.max_qa_routebacks,
            timeout_s=args.run_timeout,
            mem_limit_gb=args.mem_limit_gb,
        )
        with spent_lock:
            spent += body.get("cost_usd", 0) or 0
        return body

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = {pool.submit(_run_guarded, t): t for t in tasks}
        for future in as_completed(futures):
            if future.cancelled():  # 상한 도달로 취소된 미시작 run
                continue
            result = future.result()  # 드라이버가 예외를 내부 격리+영속화 — raise 없음
            if result is None:  # 상한 도달로 미실행 skip
                continue
            body = result
            task = futures[future]
            done += 1
            crashed += body["status"] == "failed"
            with spent_lock:
                cum = spent
            label = body.get("final_status") or body.get("error", "?")
            print(
                f"[batch] ({done}/{len(tasks)}) {task.seed.value} r{task.run_index} "
                f"→ {label} ({body['batch']['elapsed_s']}s, ${body.get('cost_usd', 0)}, "
                f"누적 ${cum:.2f})",
                flush=True,
            )
            if db_engine is not None:
                # 적재 실패가 배치를 죽이지 않게 — run 파일은 이미 영속됨(재적재 가능).
                try:
                    if difficulty_llm is not None and body.get("package") is not None:
                        body = {
                            **body,
                            "package": annotate_difficulty(
                                body["package"], llm=difficulty_llm
                            ),
                        }
                    pid = persist_run(db_engine, body)
                    if pid:
                        print(f"[batch]   ↳ DB problem_id={pid}", flush=True)
                except Exception as exc:  # noqa: BLE001 — DB 장애 격리
                    print(
                        f"[batch]   ⚠ DB 적재 실패 ({type(exc).__name__}: {exc}) — "
                        "run 파일 보존, 재적재 가능",
                        flush=True,
                    )
            # 비용 상한 — 도달 시 미시작 run 취소 (실행 중인 건 끝나게 둠). guard 가
            # 1차 차단하므로 cancel 은 큐 정리·로그용 최적화.
            if budget and cum >= budget and not budget_stopped:
                budget_stopped = True
                n_cancel = sum(1 for f in futures if f.cancel())
                print(
                    f"[batch] ⚠ 비용 상한 ${budget:g} 도달 (누적 ${cum:.2f}) — "
                    f"미시작 {n_cancel} run 중단. 올리려면 --max-cost-usd N",
                    flush=True,
                )

    summary = _aggregate(out_dir)
    _write_json(out_dir / "summary.json", summary)
    _print_report(summary)
    return 1 if crashed else 0


if __name__ == "__main__":
    sys.exit(main())
