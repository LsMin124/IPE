# IPE — Infinite Problem Engine

> **AI 멀티에이전트가 알고리즘 문제를 생성하고, 결정론적 검증으로 정답을 보증하며,
> REST API 로 서비스에 공급하는 파이프라인.** LangGraph 상태그래프 위에서 Claude
> (Opus 4.8 / Sonnet 4.6) 가 시드 → 형식 동결 → 은닉 지문 → 정해/검산 코드를 짜고,
> LLM 과 독립된 symbolic verifier · golden↔brute differential 이 정답성을 결정한다.

[![Tests](https://img.shields.io/badge/tests-903%20passed-brightgreen)](tests/)
[![mypy](https://img.shields.io/badge/mypy--strict-100%20files-blue)](pyproject.toml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![Models](https://img.shields.io/badge/Claude-Opus%204.8%20%2F%20Sonnet%204.6-8A2BE2)](ipe/v2/config.py)
[![API](https://img.shields.io/badge/contract-v3.2-orange)](docs/integration/pipeline-service-api-contract.md)

> 🌐 인터랙티브 대시보드: [lsmin124.github.io/IPE](https://lsmin124.github.io/IPE/)

---

## 무엇

IPE 는 **AI 로 문제를 만들고, 결정론으로 정답을 보증**하는 시스템이다.

- **AI 멀티에이전트 생성** — 다수의 Claude 노드가 역할을 나눠 알고리즘 문제를 설계·서술·구현
- **은닉 + 위장** — 정답 알고리즘을 숨기고 현실 도메인 스토리로 위장 (응시자가 환원을 발견해야 함)
- **결정론적 검증** — 정답성은 LLM 판단이 아니라 **symbolic verifier + golden↔brute differential** 이 결정
- **QA 비평 게이트** — 모호성·공정성·난이도·정답 유출을 병렬 critic 이 심사
- **BOJ 티어 난이도 calibration** — 생성 문제를 Bronze~Platinum 으로 자동 등급화
- **서비스 공급** — FastAPI REST API 로 백엔드에 비동기 job 형태로 제공

핵심 명제: **"생성은 AI 가, 정답 보증은 결정론이."** LLM 은 창의적 설계·서술·코딩을
맡고, 채점 정답(expected output)은 검증된 golden 코드 **실행** 으로 부트스트랩하며,
정답성은 LLM 과 독립된 알고리즘별 수학적 invariant 로 교차검증한다.

---

## 🤖 AI 활용

### 1. 멀티에이전트 파이프라인 (LangGraph 상태그래프)

문제 생성을 단일 LLM 호출이 아니라 **역할 분리된 노드들의 상태그래프**로 분해한다.
각 노드는 frozen Pydantic 상태(`V2State`)를 읽고 자기 산출만 채운다.

| 노드 | 역할 | 모델 | 종류 |
|---|---|---|---|
| **strategist** | 은닉 코어 + 합성 기법 + 위장 도메인 결정 (발산) | Sonnet 4.6 | LLM |
| **formalizer** | 입출력 형식 계약(`io_schema`) **FREEZE** | Opus 4.8 | LLM (정밀) |
| **narrative** | 은닉 지문 렌더 (도메인 스토리) | Sonnet 4.6 | LLM (창작) |
| **faithfulness** | 지문 ↔ 형식계약 충실성 round-trip 검증 | Opus 4.8 | LLM (검증) |
| **spec_bridge** | blueprint → solver spec 투영 | — | 결정론 |
| **designer** | 알고리즘 설계 + 불변식 도출 | Sonnet 4.6 | LLM |
| **golden coder ×K** | 정해 코드 (모델 다양성으로 병렬) | Opus 4.8 + Sonnet 4.6 | LLM |
| **brute coder** | 독립 무차별 검산 코드 (golden 과 distinct) | Sonnet 4.6 | LLM |
| **reconciler** | golden ↔ brute differential 합의 | — | 결정론 |
| **executor** | 샌드박스 실행 + symbolic verifier dispatch | — | 결정론 |
| **input_generator** | 시드 기반 입력 **결정론** 생성 | — | 결정론 |
| **suite_assembler** | golden 실행으로 expected 부트스트랩 | — | 결정론 |
| **qa reviewer ×N** | 모호성·공정성·난이도·유출 병렬 비평 | Sonnet 4.6 | LLM (critic) |
| **difficulty** | BOJ 티어 calibration (anchor 기반) | Sonnet 4.6 | LLM |

### 2. 모델 티어링 — 작업 난이도에 맞춘 분담

| 모델 | 쓰임 | 이유 |
|---|---|---|
| **Claude Opus 4.8** | 형식 동결(formalizer) · 충실성 검증(faithfulness) · 정해 1 | 계약을 못 박고 모순을 잡는 정밀·추론 작업 |
| **Claude Sonnet 4.6** | 전략·서술·설계·QA 비평·무차별 검산·난이도 | 발산·창작·검산은 빠르고 다양하게 |
| **모델 다양성** | golden(Opus+Sonnet) vs brute(Sonnet) | 같은 코드를 **다른 모델**이 독립 작성 → 우연한 동일 버그가 상쇄되어 differential 신뢰도↑ |

### 3. 구조화 출력 (typed tool call)

모든 LLM 노드는 자유 텍스트가 아니라 **검증된 Pydantic 스키마**로 반환한다
(`langchain_anthropic` + `with_structured_output`). 형식 위반 시 모델이 재시도하므로
파싱 실패·환각 필드가 파이프라인에 새지 않는다.

### 4. 단일-IR — consistency-by-construction

문제의 모든 표면(입력 형식 / 제약 / 지문 / 정해)을 하나의 동결 IR(`ProblemBlueprint`)
**투영**으로 만든다. 같은 사실을 여러 LLM 이 따로 서술해 어긋나던 모순(O(N²) 표면)을
구조적으로 붕괴시킨다. 예: 정렬성·문자집합·참조 의미(위치 인덱스 vs 개수)·출력 형식의
도메인 의미를 IR 한 곳에 핀하고 모든 곳이 그것을 **읽는다**.

### 5. 자기교정 루프 (faithfulness round-trip · QA back-route)

- **faithfulness**: 지문이 형식계약을 왜곡하면 narrative 를 재생성 (예산 바운드).
- **QA critic 패널**: 모호성/공정성/난이도/(은닉 시)정답유출을 병렬 심사 → fail 시
  지문/스펙을 패치하고 재리뷰하는 back-route 루프.

### 6. AI 판단 vs 결정론의 경계

> **정답성은 절대 LLM 이 결정하지 않는다.** expected output 은 검증된 golden 코드
> 실행으로 채우고, 정답 보증은 알고리즘별 수학적 invariant(예: Dijkstra ↔ Bellman-Ford
> 교차검증, segment tree ↔ naive O(NQ))로 교차검증한다. LLM 은 *무엇을 만들지* 를,
> 결정론은 *그것이 맞는지* 를 맡는다.

---

## 🔌 API 활용

### A. Anthropic API (Claude 호출)

- `langchain_anthropic.ChatAnthropic` 로 호출, 모든 노드가 `with_structured_output(...)`
  으로 **typed tool call** 반환.
- 모델 ID: `claude-opus-4-8`, `claude-sonnet-4-6` (난이도/검산은 Sonnet).
- 인증: `.env` 의 `ANTHROPIC_API_KEY`.
- 비용 실측: 1 run ≈ $0.4~0.6, 출하당 ≈ $1~2 (재시도 포함, 모드·합성수에 따라).

### B. 서비스 API (FastAPI) — 백엔드 공급 계약

파이프라인을 **stateless REST 서비스**로 노출한다. 문제 영속화는 서비스 백엔드 DB 가
전담하고, 이 서버는 진행 중 job 만 in-memory 로 유지한다.

| 메서드 · 경로 | 역할 |
|---|---|
| `GET /healthz` | 헬스체크 |
| `POST /v1/problems/generate` | 비동기 생성 시작 → `202 {job_id}`. `idempotency_key` 로 중복 방지, 용량 초과 시 `429 + Retry-After`. |
| `GET /v1/jobs/{job_id}` | job 폴링 — `running`/`completed`/`failed` + 완료 시 문제 패키지 |

요청 본문(`mode` 가 노브 4종을 결정):

```jsonc
{ "mode": "p1",                  // p1=단일·공개·QA3 / p2=합성·은닉·QA4
  "seed_algorithm": "dijkstra",  // p1=고정 타겟, p2=힌트(은닉)
  "with_qa": true,
  "idempotency_key": "req-..." }
```

```bash
# 서버 기동
IPE_API_KEY=... uv run uvicorn 'ipe.v2.api:create_app' --factory --port 8000
```

- 계약 SSOT: [`docs/integration/pipeline-service-api-contract.md`](docs/integration/pipeline-service-api-contract.md) (**v3.2**)
- 배포 가이드: [`docs/integration/pipeline-deploy.md`](docs/integration/pipeline-deploy.md) · DB 접근: [`docs/integration/db-access-handoff.md`](docs/integration/db-access-handoff.md)
- 패키지 필드(문제/정해/채점셋/메타 — `meta.difficulty`/`problem_number`/`algorithm` N:M 등)는 계약이 SSOT.

---

## 파이프라인 아키텍처 (P1 / P2 모드)

```text
START → strategist → formalizer → narrative → faithfulness ──(왜곡)─→ narrative 재생성
          (시드)      (FREEZE)     (은닉지문)   (round-trip)            (예산 바운드)
                                                    │(faithful)
                                                    ▼
   spec_bridge → designer → dispatch ─┬→ golden_0..K (Opus+Sonnet) ─┐
   (순수 투영)              (불변식)    └→ brute (Sonnet, distinct) ──┴→ reconciler
                                                                       (differential)
   reconciler ─(합의)→ sample/edge filler → executor (샌드박스 + symbolic verifier)
                                                 │(pass)
                                                 ▼
   generator_designer → input_generator → suite_assembler  (결정론 채점셋, golden→expected)
                                                 │
                                                 ▼
   qa_{ambiguity·fairness·difficulty·(leakage)} 병렬 → aggregator ─(pass)→ 출하
                          ▲ (fail) spec_patch / narrative_revise back-route ┘
```

| 노브 | **P1** (단일·공개) | **P2** (합성·은닉) |
|---|---|---|
| `hidden` | False | True |
| `composition` | 빈값 (단일 알고리즘) | ≥1 (총 2개+ 합성) |
| `qa_kinds` | ambiguity·fairness·difficulty | + leakage (4종) |
| `seed_algorithm` | 고정 공개 타겟 | 힌트 (은닉) |

자세한 노드 의미: [`ipe/v2/graph.py`](ipe/v2/graph.py) docstring.

---

## 19 Algorithm Catalog

| family | algorithm | verifier 핵심 invariant |
|---|---|---|
| Graph | Dijkstra | Bellman-Ford 교차검증 (non-negative weight) |
| Graph | BFS | Floyd-Warshall 교차검증 (edge=1) |
| Graph | Topological Sort | edges_respect_order + Kahn DAG check |
| Graph | Bellman-Ford | Floyd-Warshall 교차검증 (negative weight) |
| Graph | Floyd-Warshall | V × Bellman-Ford 교차검증 (all-pairs) |
| Graph | Kruskal MST | Prim 교차검증 |
| Graph | Max Flow | brute min-cut (max-flow min-cut 정리) |
| Search | Binary Search | linear scan 교차검증 |
| Search | LIS | patience sort O(N log N) |
| Array | Two Sum | brute O(N²) pair 열거 |
| Sort | Sort cluster | Python `sorted()` |
| String | String Match cluster | brute O(NM) substring search |
| DS | Segment Tree | naive O(NQ) range sum |
| DS | Heap (Min-PQ) | sorted-list 시뮬레이션 |
| DS | Fenwick Tree (BIT) | naive prefix-sum |
| DSU | Union-Find | BFS over union edges |
| DP | Knapsack 0/1 | brute O(2^N) subset 열거 |
| DP | Coin Change | DP O(N*A) tabulation |
| NumTheory | Sieve of Eratosthenes | trial division O(N√N) |

각 algorithm 의 **수학적 invariant** 를 코드로 결정론 검증 (LLM 판단과 독립한 anchor).

---

## Quick Start

```bash
# 환경 설정
uv sync --extra dev
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# 단일 문제 생성 (P1 단일·공개 / P2 합성·은닉)
uv run python -m ipe.v2.main_v2 --algorithm dijkstra --mode p1
uv run python -m ipe.v2.main_v2 --algorithm dijkstra --mode p2 --max-iter 6 --verbose

# 배치 측정 (시드 × N, 출하율/비용 집계)
uv run python -m ipe.v2.batch --seeds binary_search,lis --runs-per-seed 3 --mode p1 \
  --out outputs/my-run

# API 서버 (서비스 백엔드 공급)
IPE_API_KEY=... uv run uvicorn 'ipe.v2.api:create_app' --factory --port 8000

# 난이도 백필 (DB 적재 문제를 BOJ 티어로 calibration)
uv run python -m ipe.v2.difficulty --db-url postgresql://... [--force]
```

지원 `--algorithm` (seed): `dijkstra` `bfs` `toposort` `bellman_ford` `floyd_warshall`
`kruskal_mst` `max_flow` `binary_search` `lis` `two_sum` `sort` `string_match`
`segtree` `heap` `fenwick` `union_find` `knapsack` `coin_change` `sieve`

---

## 문서 (SSOT)

| 위치 | 역할 |
|---|---|
| [`docs/SPEC.md`](docs/SPEC.md) | 기능/비기능 요구사항 |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 모듈 설계 + Pydantic schema SSOT |
| [`docs/PRINCIPLES.md`](docs/PRINCIPLES.md) | 운영 룰 (N≥3 측정 / baseline anchor / RCA rollback 등) |
| [`docs/integration/pipeline-service-api-contract.md`](docs/integration/pipeline-service-api-contract.md) | 파이프라인 ↔ 서비스 백엔드 API 계약 (v3.2) |
| [`ipe/v2/graph.py`](ipe/v2/graph.py) | v2 LangGraph 위상 (P1/P2 노브) |
| [`CHANGES.md`](CHANGES.md) | 변경 이력 |

---

## 개발

```bash
make ci   # ruff + mypy --strict + pytest (903 passed)
uv run pytest tests/v2 -q
uv run python -m ipe.v2.main_v2 --algorithm sieve --mode p1 --verbose
```

운영 룰 → [`docs/PRINCIPLES.md`](docs/PRINCIPLES.md). 측정은 N≥3 + baseline anchor,
과측정은 diminishing returns 로 중단하는 것이 이 저장소의 규율이다.
