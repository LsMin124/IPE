# IPE 파이프라인 ↔ 서비스 백엔드 API 계약 (v3.0)

> **문서 상태**: 확정 — 2026-06-22 (v3.0), 파이프라인 측 작성.
> **대상 독자**: 서비스 백엔드(BOJ 유사) 구현 개발자.
> **변경 절차**: 본 문서가 계약의 단일 진실원천. 필드 추가는 minor(하위호환),
> 제거/의미 변경은 major + 양측 합의.
> **⚠ v3.0 (breaking)**: 알고리즘 분류가 `problems.algorithm` 스칼라 → `problem_algorithms`
> N:M 정션(코어+합성 전부, role 구분 — §3.4). (v2.0: `mode` `hidden`/`direct`→`p1`/`p2`.)
> 백엔드 미연동 시점이라 양측 합의 없이 반영 — 소비자 없음. 전체 변경이력 §7.

---

## 0. 시스템 경계와 책임 분리

```
[서비스 백엔드 (별도 서버)]                  [IPE 파이프라인 서버 (stateless)]
  문제/채점셋/제출/유저 DB  ◀── 영속화 전담      문제 생성 (LLM + 검증 + QA)
  채점기 (유저 제출 채점)                       생성 중 검증용 sandbox (내부)
  생성 배치 워커            ── HTTP/JSON ──▶    API 3 엔드포인트만 노출
  검수 큐 → 공개
```

| 책임 | 소유 |
|---|---|
| 문제 생성·검증·QA·채점셋 조립 | 파이프라인 |
| 문제·채점셋·제출 **영속화** | 백엔드 DB |
| 유저 제출 **채점** | 백엔드 채점기 |
| 생성 요청 큐잉·재시도·배치 스케줄 | 백엔드 워커 |
| 검수(draft→published) 워크플로 | 백엔드 |

파이프라인은 **무상태**: 생성 결과를 영속하지 않으며, 진행 중 job 상태만
메모리에 유지한다 (서버 재시작 시 유실 — §3 재시도 규약으로 흡수).

---

## 1. 공통 규약

- Base URL: 배포 시 공유 (예: `https://pipeline.internal:8000`)
- 인증: 모든 요청에 `X-API-Key: <static key>` 헤더. 불일치 시 `401`.
- Content-Type: `application/json; charset=utf-8`
- 모든 텍스트는 UTF-8. 문제 지문은 한국어.
- 버저닝: URL prefix `/v1` + 패키지 내 `meta.package_version`.

---

## 2. 엔드포인트

### 2.1 `POST /v1/problems/generate` — 생성 job 시작

요청:

```jsonc
{
  "mode": "p2",                     // "p1"(단일·공개·토픽드릴) | "p2"(합성·은닉·모의고사) — 아래 모드 표
  "seed_algorithm": "dijkstra",     // §2.4 enum 중 하나 (필수)
  "with_qa": true,                  // 기본 true — QA 게이트 포함 (권장)
  "max_qa_routebacks": 1,           // QA fail 시 자동 회수 시도 횟수 (기본 1)
  "idempotency_key": "uuid-..."     // 필수 — 재시도 이중과금 방지
}
```

**모드(`mode`)** — 생성 파이프라인은 정확히 2종이며 모드가 4개 노브를 한 번에 정한다:

| mode | seed_algorithm | 합성 | 지문 | QA 관점 | 용도 |
|---|---|---|---|---|---|
| `p1` | **공개 타겟**(고정) | 단일(없음) | 알고리즘 명시 가능 | 3종 (ambiguity/fairness/difficulty) | 토픽 드릴 |
| `p2` | **은닉 힌트** | 2+ 결합 | 알고리즘 미명시(은닉) | 4종 (+leakage) | 모의고사 |

`seed_algorithm` 은 두 모드 다 §2.4 enum 에서 받되, `p1` 은 그 알고리즘을 공개 타겟으로
고정하고 `p2` 는 은닉할 힌트로만 쓴다. 패키지 메타도 모드를 따른다: `meta.composition` 은
`p1`=항상 `[]` / `p2`=1개 이상, `meta.qa.verdicts` 의 키 집합은 모드 QA 관점을 따른다
(`p1` 은 `leakage` 키 없음).

응답:

- `202 Accepted` `{ "job_id": "..." }`
- 동일 `idempotency_key` 의 job 이 살아 있으면 **기존 `job_id` 를 반환** (202).
  서버 재시작 후에는 idempotency 미보장 — 같은 key 라도 새 job 이 생성될 수 있다.
- `422` 요청 스키마 위반 (unknown mode/seed 등)
- `429` 동시 생성 슬롯 초과 — `Retry-After: <sec>` 헤더 포함. 워커는 backoff 후 재요청.

### 2.2 `GET /v1/jobs/{job_id}` — 상태/결과 조회

```jsonc
// 진행 중
{ "status": "running" }

// 완료 — 성공 (출하 가능)
{
  "status": "completed",
  "final_status": "success",
  "package": { /* §2.5 ProblemPackage */ }
}

// 완료 — QA 게이트만 불통 (문제+채점셋은 완성 — draft 검수 대상)
{
  "status": "completed",
  "final_status": "fail_qa",
  "package": { /* §2.5 — qa.overall_pass=false, verdicts/findings 포함 */ }
}

// 완료 — 그 외 fail (산출물 미완성 — 패키지 없음, 재시도 대상)
{
  "status": "completed",
  "final_status": "fail_verification",   // §2.3 참조
  "package": null,
  "diagnostics": { "summary": "...", "detail": "..." }
}
```

- `404` — 모르는 job_id (서버 재시작으로 유실 포함). 워커는 **새 generate 로 재시도**.
- 폴링 권장 주기: 15~30초 (1 run 은 2~6분 소요).

### 2.3 `final_status` 의미론 — **fail 은 에러가 아니다**

생성은 확률적 공정이며 fail 은 게이트가 일한 결과다. 워커는 fail 이면
같은 seed 로 새 run 을 시도하면 된다 (권장 재시도 예산: seed 당 3~5회).

| final_status | 의미 | package | 백엔드 처리 |
|---|---|---|---|
| `success` | 검증+채점셋+QA 전부 통과 | 포함 | `draft` 적재 (또는 정책상 바로 `review`) |
| `fail_qa` | 문제·채점셋 완성, QA 관점(p1 3종/p2 4종) 중 일부 불통 | **포함** (QA 리포트 동봉) | `draft` 적재 → **사람 검수로 구제 판단** |
| `fail_verification` | 정답 코드가 샘플 검증 불통 | null | 재시도 |
| `fail_synthesis_rejected` | 독립 정답 후보 간 불합의 | null | 재시도 |
| `fail_faithfulness` | 지문이 형식 계약을 왜곡 | null | 재시도 |
| `fail_spec_authoring` | 문제 명세 LLM 산출 실패 | null | 재시도 |
| `fail_budget_exhausted` | 내부 반복 예산 소진 | null | 재시도 |

> `fail_qa` 구제 운영 권고: leakage(유출) 단독 fail 은 판정 변동이 관측된
> 클래스라 사람 검수 가치가 높다(p2 만 — p1 은 leakage 미실행). ambiguity(모호성)
> fail 은 findings 를 보고 지문 수동 보정 가능 여부로 판단.

### 2.4 `seed_algorithm` enum (19종 — 전부 허용)

```
dijkstra, bfs, bellman_ford, floyd_warshall, kruskal_mst, max_flow,
toposort, union_find, sort, lis, knapsack, coin_change, binary_search,
two_sum, segtree, fenwick, heap, sieve, string_match
```

초기 문제 은행 배치는 출하 실측이 축적된 시드(`dijkstra` 등 graph 계열,
`sort`, `bfs`)부터 채우고 나머지로 점진 확장 권고 (시드별 출하율 변동 존재).

### 2.5 ProblemPackage 스키마

```jsonc
{
  "problem": {
    "title": "상수도 배관망 점검",
    "description": "…(지문 전문, 한국어. hidden 모드면 알고리즘명 미포함)…",
    "io_contract": {
      "input_format": "…(줄 단위 형식 명세 — 코드가 렌더한 canonical, 신뢰 가능)…",
      "output_format": "단일 정수 한 줄",
      "example_separator": "newline"
    },
    "constraints": [
      { "name": "V", "min_value": 2, "max_value": 100000, "description": "" }
    ],
    "sample_testcases": [
      { "input_text": "3 2\n1 2 5\n2 3 7\n1 3", "expected_output": "12", "description": "" }
    ]
  },
  "solution": {                             // ⚠ 내부 전용 — 응시자 비노출
    "golden_code": "import sys\n…(검증된 정해 소스 전문)…",
    "language": "python"                    // "python" | "java"
  },
  "test_suite": {
    "cases": [
      {
        "input_text": "…",
        "expected_output": "…",            // success: 항상 채워짐 / fail_qa 초안: 빈값 가능 (아래 규약)
        "category": "scale:small",          // scale:* | edge:* — 분포 진단용
        "golden_elapsed_ms": 42             // 이 케이스의 golden 실행시간 (TL 산정 근거)
      }
    ],
    "origin": "claude-opus-4-8"             // expected 를 채운 검증된 golden 의 출처
  },
  "meta": {
    "package_version": "1.0",
    "mode": "p2",                           // "p1" | "p2" (§2.1 모드 표)
    "hidden_algorithm": "dijkstra",         // ⚠ 내부 전용 — 유저 응답에서 제외할 것
    "composition": ["union_find", "toposort"],  // ⚠ 내부 전용 (합성 기법; p1=[], p2=1개+)
    "domain": "waterworks",
    "difficulty": {                         // ⚠ 내부 메타 — RFC R4 사후 calibration (옵션: 켜진 경우만)
      "label": "Gold IV",                   // BOJ 티어 라벨 → problems.difficulty 1급 컬럼 승격
      "tier": "Gold",                       // label 파생 그룹 (필터·집계용)
      "reasoning": "bj_1753_gold5 와 동형 — 단일 출발점 다익스트라, V≈20000",
      "factors": { "algorithm": "dijkstra", "complexity": "O((V+E) log V)", "n_max": 20000, "data_structures": ["priority_queue"] },
      "calibration_anchors": ["bj_1753_gold5"]   // 비교에 쓴 anchor id
    },
    "golden_language": "python",            // TL 언어 보정 기준
    "qa": {
      "overall_pass": true,
      "verdicts": { "ambiguity": true, "fairness": true, "leakage": true, "difficulty": true },  // p1 은 leakage 키 없음 (3종)
      "findings": [                          // fail_qa 일 때 검수 근거 (전문)
        { "kind": "ambiguity", "severity": "blocker", "description": "…" }
      ]
    },
    "verification": { "overall_pass": true },
    "timing": { "max_golden_elapsed_ms": 180 },   // suite 전 케이스 중 최대
    "generation": { "elapsed_s": 272, "iteration": 1, "qa_routebacks": 1 }
  }
}
```

필드 규약:

- **`meta.hidden_algorithm`/`meta.composition` 은 유저에게 절대 노출 금지**
  (노출 = 은닉·유출 방지 설계 무력화). 내부 운영 DB 에서는 알고리즘 분류 필터링용으로
  `problem_algorithms` 정션(코어=role `core`, 합성=role `composition`)에 적재된다(§3.4) —
  이 역시 내부 전용.
- **`solution.golden_code` 은 내부 검수·재현용 정해 — 응시자에게 절대 노출 금지**
  (채점은 `test_suite` 줄 단위 exact-match 라 정해 코드 불요; DB 내부 컬럼·감사용).
  `package_version` 무변경 `1.0` 에 **additive** 로 추가 — 기존 백엔드는 무시해도 안전.
  `solution` 은 `package` 가 존재할 때만(=`success`/`fail_qa`) 동봉되며, 이론상
  정해 부재 시 `null`.
- `p1`(공개) 모드에서는 지문이 알고리즘을 명시할 수 있음 — 노출 정책은 백엔드
  product 단 결정.
- `test_suite.cases` 수는 보장값 없음 (관측 40~60). 케이스 순서는 의미 없음.
- **채점 가능 보장은 `final_status == "success"` 패키지에 한함.** success 의 모든
  케이스는 검증된 golden 출력으로 `expected_output` 이 채워진다(정해 출력이 실제로
  빈 문자열인 퇴화 케이스 예외 — 이때도 exact-match 유효). **`fail_qa` 패키지의
  `test_suite` 는 초안(검수 대상)이라 일부 케이스 `expected_output` 이 빌 수 있으므로
  채점에 그대로 사용 금지** — `fail_qa` 는 사람 검수로 구제·재생성하고, 구제 시
  채점셋 재조립이 필요하다.
- **`meta.difficulty` (RFC R4 사후 calibration)** — BOJ 표준 anchor 대비 측정한 티어
  주석. **옵션 필드**: 파이프라인이 `--with-difficulty`(배치/ingest) 또는
  `IPE_WITH_DIFFICULTY`(API)로 켜진 경우에만 동봉되며, 아니면 키 자체가 없다(additive,
  `package_version` 무변경 — 기존 백엔드는 무시해도 안전). `label`(예: `"Gold IV"`)은
  `problems.difficulty` 1급 컬럼으로 승격(쿼리·필터·집계용), 전체 report 는
  `internal_meta.difficulty` 에 보존. **내부 운영 메타** — 난이도 표시 자체는 보통 노출
  가능하나 `reasoning`/`factors` 는 정해 단서가 될 수 있어 비노출 권장(노출 정책은 백엔드
  product 단 결정). **출하 게이트가 아니라 측정값** — 모든 난이도가 양산 대상이며 난이도로
  reject 하지 않는다(퇴화/모순 게이트는 QA `difficulty` charter 가 별도로 담당).

### 2.6 `GET /healthz`

`200 { "status": "ok" }` — 로드밸런서/모니터링용. 인증 불요.

---

## 3. 백엔드 구현 규약 (워커)

1. **재시도**: `429`→backoff 재요청 / `404`(job 유실)→새 generate / `fail_*`→
   재시도 예산 내 새 generate / `5xx`·timeout→지수 backoff 후 재시도 (이때만
   장애로 집계).
2. **idempotency_key**: 워커 재시작 대비 요청마다 신규 UUID 발급·저장.
3. **원본 보관 권장**: 수신한 package 원문(jsonb)을 `generation_requests` 류
   테이블에 보관 — 감사/재검수/재적재 근거.
4. **DB 최소 스키마 제안**:
   - `problems(id, title, description, input_format, output_format,
     constraints jsonb, samples jsonb, internal_meta jsonb, status
     draft|review|published, time_limit_ms, created_at)`
   - `test_cases(problem_id, seq, input text, expected text, category)`
   - `problem_algorithms(problem_id, algorithm, role)` — 문제↔알고리즘 N:M
     (PK `(problem_id, algorithm)`). `role` = `core`(은닉 코어) | `composition`(합성 기법),
     둘 다 §2.4 enum 어휘. **알고리즘 분류 필터링**용(특정 기법이 코어든 합성이든 포함된
     문제 조회). 응시자 비노출. v3.0 에서 구 `problems.algorithm` 스칼라(코어만)를 대체.
   - `generation_requests(idempotency_key, seed, mode, job_id, final_status,
     attempts, raw_package jsonb, created_at)`

### 3.5 파이프라인 직접 적재 모드 (운영 토폴로지 옵션 — §0 경계 변경)

초기 운영(파이프라인 서버 / 서비스 백엔드 서버 / **공유 DB 1대**)에서는 백엔드가
생성 워커를 따로 두지 않고, **파이프라인이 공유 PostgreSQL 에 직접 적재**할 수 있다.
이때 §0 의 "영속화 전담=백엔드"가 "**문제·채점셋 적재=파이프라인 / 읽기·승격·채점
=백엔드**"로 바뀐다.

- 적재 경로: 배치 CLI `python -m ipe.v2.batch --seeds … --db-url \
  postgresql+psycopg://user:pw@host:5432/bank` — 완료 run 마다 패키지를 위 스키마에
  적재(run JSON 파일과 병행). **idempotent**(같은 run_id 재적재 시 중복 문제 생성 없이
  attempts 증가).
- **스키마 소유 = 파이프라인**(alembic): 배포 시 `IPE_DB_URL=… alembic upgrade head`
  로 테이블 생성/이행. 위 §3-4 스키마가 실제 구현(`problems`/`test_cases`/
  `problem_algorithms`/`generation_requests`)이며 SSOT 는 `ipe/v2/db/schema.py`. 서비스 백엔드는 이 테이블을
  **읽기 전용**으로 소비(특히 `problems.status='draft'` 만, `test_cases` 는 success
  문제만 채점 — §2.5 규약).
- 매핑: `problems.internal_meta`=패키지 `meta` 전체(내부전용), `solution_code`=정해
  (응시자 비노출), `time_limit_ms`=max(1000, `meta.timing.max_golden_elapsed_ms`×3).
- HTTP API(§2)는 **무상태 유지** — 온디맨드 생성용. 직접 적재는 배치(은행 적재)
  경로에만 적용된다.

---

## 4. 채점 연동 규약

- **비교 방식**: 줄 단위 exact match. 각 줄의 trailing whitespace 와 출력
  끝의 trailing newline 은 무시(정규화 후 비교). 그 외 공백·대소문자 변형 없음
  — expected 는 검증된 golden 의 stdout 그대로라 형식이 안정적.
- **유일답 보장**: 파이프라인이 생성 단계에서 유일답을 강제하므로 special
  judge 불필요.
- **TL 산정**: `time_limit_ms = max(기본하한, meta.timing.max_golden_elapsed_ms × 배수)`
  권장 배수 3~5, 기본하한 1000ms. golden 은 Python(`meta.golden_language`) —
  타 언어 허용 시 언어별 배수 보정은 백엔드 정책.
- **ML(메모리)**: 파이프라인 미제공 — 백엔드 고정 기본값(예: 256MB) 적용.

---

## 5. 운영 파라미터·용량 계획

| 항목 | 값 |
|---|---|
| 1 run 소요 | 2~6분 (관측 74~343s) |
| 1 run 비용 | **~$0.4~0.6** (토큰 계측 실측 N=2: 상류 reject $0.40 / QA+back-route 풀 경로 $0.53 — 모델별 토큰 × 공식 단가) |
| run 당 출하 성공률 | 변동 30~50% (시드 의존) |
| **출하 문제 1개당 실효** | **~$1~2, 10~20분** |
| 파이프라인 동시 생성 슬롯 | 2~3 (초과 시 429) |

→ 비용 제약은 사실상 미미 (예: 100문제 은행 ≈ $100~200). 병목은 **시간**:
런칭에 필요한 문제 수 N 이 정해지면 `N × 15분 / 슬롯수` 로 배치 기간 역산.
런칭 직전 몰아치지 말고 **지금부터 배치 적재 시작** 권장.

---

## 6. 파이프라인 측 구현 상태와 일정

| 항목 | 상태 |
|---|---|
| 생성 파이프라인 (검증+채점셋+QA+자동회수) | ✅ 완성, 실 LLM success run 보유 |
| `fail_spec_authoring` 가드 (crash 무발생 보장) | ✅ 머지됨 |
| API 서버 (본 계약 3 엔드포인트) | 🔨 Slice 1 — 계약 확정 후 즉시 (반나절~1일) |
| `golden_elapsed_ms` 메타 | 🔨 Slice 1 에 포함 |
| 컨테이너/배포 | 🔨 Slice 2 (반나절) |

---

## 7. 변경이력

| 버전 | 일자 | 변경 |
|---|---|---|
| **v3.0** | 2026-06-22 | **[breaking]** 알고리즘 분류가 `problems.algorithm` 스칼라(코어 1개) → `problem_algorithms` N:M 정션으로 교체(§3.4). 코어(role `core`) + 합성(role `composition`, §2.1 P2) 전부 행으로 — 백엔드가 합성 기법까지 필터 가능. 패키지(`meta.hidden_algorithm`/`composition`)는 불변, DB 적재 형상만 변경. 백엔드 미연동 시점 반영(소비자 없음). |
| **v2.0** | 2026-06-22 | **[breaking]** `mode` enum `hidden`/`direct` → `p1`/`p2` (§2.1). 의미 변경=major. `p1`=단일·공개·QA 3종 / `p2`=합성·은닉·QA 4종 — 모드가 합성/은닉/지문/QA관점 4노브를 한 번에 결정. 패키지 `meta.mode`·`meta.composition`·`meta.qa.verdicts` 도 모드에 종속. 백엔드 미연동 시점 반영(기존 v1.0 소비자 없음).<br>**[additive]** `meta.difficulty`(RFC R4 사후 calibration, 옵션 — `--with-difficulty`/`IPE_WITH_DIFFICULTY` 켤 때만) + 직접 적재 DB 의 `problems.algorithm`·`problems.difficulty` 1급 컬럼. 키/컬럼 부재 시 무시 안전(하위호환). |
| v1.0 | 2026-06-12 | 최초 확정 — 3 엔드포인트(§2), ProblemPackage 스키마(§2.5), 직접 적재 모드(§3.5). |

문의: 파이프라인 측 (본 repo). 계약 개정은 본 문서 PR 로.
