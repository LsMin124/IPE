# RFC: Easy Problem Track — 단일 엔진 + 난이도 노브 (no pipeline fork)

작성 2026-06-25 · 상태: 제안(승인 대기) · 관련 [[single-ir-architecture-rfc]] · [[backbone-generalization-rfc]]

## 0. Thesis

초급 문제(기본 입출력 · 산술/논리 · 큐/스택 기초)는 **별도 파이프라인이 필요 없다.**
v2 엔진은 이미 trivial 문제를 end-to-end 로 처리한다(스칼라→NullBackbone, sample-only
reconcile, difficulty-agnostic QA). 막힌 곳은 엔진이 아니라 둘뿐이다:

1. **타깃 어휘** — "무엇을 만들지"에 초급 카테고리가 없다.
2. **난이도 입력** — authoring 을 작고 명확한 문제로 *조종할* 손잡이가 없다.

둘 다 **하나의 엔진에 노브로** 추가한다 — P1/P2 가 쓰는 "one engine, modes as knobs"
패턴 그대로. **포크되는 경로가 없다.** 3세대→2 파이프라인 수렴과 단일-IR 의 통합 노력을
되돌리지 않는 것이 본 RFC 의 제1 제약이다.

## 1. Current state (grounded)

### 1.1 엔진은 trivial 을 이미 처리한다 (변경 0 영역)
- **Backbone**: 스칼라/단순 필드 → `resolve_backbone` 가 first-owns-wins, else `NullBackbone`
  (`ipe/v2/backbone/__init__.py:24-38`). NullBackbone 은 **안전 no-op** — `structural_facts()→[]`,
  `derive_edge_inputs()→()` (`ipe/v2/backbone/base.py:93-108`). 실패 아님, 폴백.
- **Reconcile**: 엣지 0 → samples-only differential. brute==golden 합의도 **유효 채택**
  (`ipe/v1/verification/reconcile.py:129-136`, `ipe/v2/nodes/reconciler.py:57-60`).
  A+B 처럼 brute 가 golden 과 동일 코드여도 reject 아님.
- **edge_filler**: `resolved_edges` 빈 경우 no-op (`ipe/v2/nodes/edge_filler.py:47-48`).
- **input_gen**: sized 필드 없으면 단일 `nominal` family 4 cases (`ipe/v2/generation/input_gen.py:430-465`),
  int 스칼라는 `value_range`+tier 로 단일 정수 직렬화 (`input_gen.py:629-630`).

### 1.2 QA 는 쉬운 문제를 막지 않는다
- difficulty 리뷰어 charter: **"명백한 모순만" 본다 — 입력 무관 상수 출력 / 명세상 불가능.
  난이도 측정·calibration 은 범위 밖** (`ipe/v2/nodes/qa_reviewer.py:51-55`).
- 파이프라인은 설계상 **난이도-agnostic** (`ipe/v2/difficulty.py:3`). calibration 은 사후 메타,
  출하 게이트 아님.
- P1 QA = `("ambiguity","fairness","difficulty")` (`ipe/v2/config.py:28`). 최소 난이도 floor
  없음 (`ipe/v2/router.py:60-75`).
- Bronze 앵커 **이미 존재**: `ipe/calibration/anchors.json` `bj_1000_bronze5`(A+B, O(1), "implementation").

### 1.3 갭 (본 RFC 가 메우는 것)
- **G1 어휘**: `TargetAlgorithm` 은 평면 19종, 메타 0 (`ipe/v1/schema/problem_spec.py:19-45`).
  전부 중급+. 초급 카테고리 없음. seed 가 유일 주입점(`ipe/v2/main_v2.py`, `batch.py:_parse_seeds`).
- **G2 난이도 입력 없음**: strategist/formalizer 에 "쉽게/작게" 지시가 **전무**
  (`strategist.py:139-169`, `formalizer.py:23-133`). 난이도는 순수 사후(`difficulty.py`).
- **G3 큐/스택 표현**: "N개 연산 처리(push/pop)"용 `operation_sequence` IOFieldType 부재
  (`ipe/v1/schema/blueprint.py:27-37`). int_array 우회는 가능하나 비-pedagogical.

## 2. Design: one engine, two orthogonal knobs

기존 mode 노브와 **직교**하는 난이도 노브를 추가한다. mode 는 그대로 둔다.

| 노브 | 값 | 의미축 | 상태 |
|---|---|---|---|
| `mode` | `p1` / `p2` | hidden·composition·qa_kinds (= 은닉/합성으로 어렵게) | 기존(`config.py:mode_knobs`) |
| `difficulty_target` | `standard` / `easy` | authoring scale·clarity (= 작고 명확하게) | **신규** |

- **초급 문제 = `mode=p1` + `difficulty_target=easy`.** (단일·공개·QA3 그대로.)
- **`difficulty_target=standard` = 기본값 = 현 동작 byte-identical**(easy 지시 미주입).
  → E1 은 단일-IR Phase 1a 처럼 "기본값=현 상수 ⇒ 무회귀" 로 검증.
- `p2`(합성·은닉)는 본질적으로 non-easy → validator 가 `p2 + easy` 조합 reject(§4).
- 난이도는 별도 *mode 값*(p0 등) 이 아니다 — easy 의 (hidden,composition,qa) 는 정확히 P1 과
  같으므로, 난이도는 mode 와 **독립 축**이지 mode 의 세 번째 값이 아니다.

### 2.1 단일소스 표 (every fact ← 정확히 1 저작 소스)
단일-IR 운영 불변식("모든 사실은 저작 소스 정확히 1개")을 초급 축에도 적용:

| 사실 | 단일 저작 소스 | 소비처 |
|---|---|---|
| 난이도 타깃 | request `difficulty_target` (입력) | state → strategist/formalizer 프롬프트 |
| easy 전략 지시 | `strategist._DIFFICULTY_DIRECTIVE_EASY` 상수 1개 | strategist user prompt(조건부) |
| easy 형식 지시 | `formalizer` easy 프롬프트 섹션 1개 | io_schema 저작 |
| 크기 경계 | formalizer io_schema(`size_range`/`value_range`) — 기존 단일소스 | render_*/input_gen 순수 투영 |
| 카테고리 family | `target_family(t)` 분류 맵 1개 (§3) | strategist/formalizer 조건부 |
| 입력 의존성 요구 | formalizer easy 지시 1줄 + QA difficulty 백스톱 | 저작 + 게이트 |

## 3. Vocabulary modeling — ⚠️ 유일한 결정 포인트

초급 카테고리를 어디에 둘지. **두 안 모두 파이프라인은 하나**(그래프 무포크) — 차이는 타입 위생뿐.

### Option A (권장) — `TargetAlgorithm` 확장 + 분류-as-data
- enum 에 초급 카테고리 추가: `BASIC_IO`, `ARITHMETIC`, `CONDITIONAL`, `LOOP_ACCUMULATE`.
- **메타는 코드 분류 맵으로** (enum 오염 최소): `target_family: dict[TargetAlgorithm, Family]`
  + `is_basic(t) -> bool` (단일소스). Family = graph/sequence/string/dp/number_theory/**basic**.
- seed 필드 타입 **불변** → main_v2/batch/state/strategist 플러밍 **무수정**. verifier 는
  None-dispatch(초급=symbolic verifier 없음, golden/brute 가 검증 — 기존 v2 dormant 패턴).
- 비용: "TargetAlgorithm" 이 비-알고리즘 보유(네이밍 smell). **선례**: anchors.json 가 A+B 를
  `"algorithm":"implementation"` 로 이미 분류 — 난이도층은 이미 "implementation" 을 멤버로 취급.

### Option B — 형제 `SkillTarget` enum + union seed 타입
- 의미 깔끔(`TargetAlgorithm` 순수 유지). 비용: seed 타입 `TargetAlgorithm | SkillTarget`
  union → 타입 표면 ~6곳 수정(`GenerateRequest.seed_algorithm`, `V2State`, `_parse_seeds`,
  `_parse_target_algorithm`, strategist, batch). 그래프는 여전히 무포크.

**권장 = A** (사용자 일관성 제약에 가장 부합·플러밍 0·분류는 data). 네이밍 caveat 는 분류 맵이
의미를 명시하므로 수용 가능. 첫 카테고리 셋: `basic_io · arithmetic · conditional · loop_accumulate`
(스칼라/배열만 — 엔진 검증 완료 영역). 큐/스택은 §5 E3.

## 4. Node → role mapping

| 노드/모듈 | 변경 | 비고 |
|---|---|---|
| `graph.py`(배선)·synthesis·reconciler·executor·QA wiring·calibration·backbones | **무수정** | 초급은 동일 경로 통과(§1.1) |
| `strategist.py` | + `_DIFFICULTY_DIRECTIVE_EASY` 조건부 섹션 | easy: composition 빈값 강제·camouflage 끔·명확 직접 서술. `_COMPOSITION_DIRECTIVE_*` 와 동형 |
| `formalizer.py` | + easy 프롬프트 섹션 | 작은 `size_range`/`value_range`·단순 io·출력 단순·**입력 의존 필수**(상수출력 금지) |
| request/state | + `difficulty_target: Literal["standard","easy"]="standard"` | 기본=standard ⇒ byte-identical |
| `config.py` | + `difficulty_knobs(target)` (또는 mode_knobs 형제) | 단일소스 노브 파생 |
| validator(Phase 2) | (선택) `p2+easy` reject + `easy ⇒ size_range ≤ cap` assert | construction-enforced 난이도(§6 R3) |

## 5. Migration plan (each shippable + measurable)

- **E0** — 본 RFC + §3 결정 확정. (코드 0)
- **E1 — 난이도 노브 (키스톤)**: `difficulty_target` 필드 + state 스레딩 + strategist/formalizer
  조건부 지시 + 기본 standard. **측정**: 기존 시드 standard 로 재생성 → **byte-identical**(무회귀 게이트).
- **E2 — 초급 어휘**: §3 카테고리 + `target_family`/`is_basic` 분류 + None-dispatch.
  **측정**: 초급 P1 출하율 N≥3(가설: graph 대비 **높음** — 모순 표면 소멸) + calibration(가설: Bronze 안착).
- **E3 — 큐/스택**: 표현 결정 = `operation_sequence` IOFieldType + `OperationBackbone`
  (SequenceBackbone 미러) **vs** int_array 우회. **측정**: 동일.
- **E4 — 은행 적재 + Bronze 서브티어**: 초급 출하분 prod 적재 + (필요시) Bronze IV~I 앵커 보강
  (현 Bronze 앵커 희소 → 서브티어 해상도).

## 6. Risks & trade-offs

- **R1 상수출력 충돌(지배)**: difficulty 게이트가 유일하게 막는 게 "입력 무관 상수 출력".
  순수 "Hello World 출력"형 기본-I/O 는 **reject**. → formalizer easy 지시에 **입력 의존 필수**
  규칙(echo/format/연산) + QA 백스톱. consistency-by-construction(프롬프트) + 게이트(backstop).
- **R2 enum 네이밍(Option A)**: cosmetic. 분류 맵이 의미 명시로 상쇄.
- **R3 LLM 이 small-size 지시 무시**: 측정으로 관측 → 드리프트 시 validator 에 `easy ⇒ size cap`
  assert 로 construction-enforced 승격(prompt-enforced → 코드강제).
- **R4 기존 BOJ trivial 중복**(A+B=BOJ 1000): domain/framing 변주 + 은행 de-dup. 초급은 본질상
  유사도 높음 — 다양성은 domain palette 회전으로 일부 완화.
- **R5 brute==golden 약한 교차검증**: trivial 은 golden==brute 흔함 → 검증 신호 감소(RFC §7.4
  '원점 라벨' 독립성은 유지되나 코드 동일). 수용 — 대신 input_gen nominal 4 cases 가 커버.

## 7. Key files this RFC touches
- `ipe/v1/schema/problem_spec.py` (TargetAlgorithm + 분류 맵, Option A)
- `ipe/v2/nodes/strategist.py` (easy directive), `ipe/v2/nodes/formalizer.py` (easy io 지시)
- `ipe/v2/config.py` (difficulty_knobs), request/`V2State` (difficulty_target)
- (선택) `ipe/v2/nodes/validator` (p2+easy reject, size cap)
- (E3) `ipe/v1/schema/blueprint.py` (operation_sequence), `ipe/v2/backbone/operation.py`
- **무수정 보존**: `ipe/v2/graph.py`, synthesis/reconciler/executor, QA wiring, `ipe/v2/difficulty.py`, 기존 backbones

## 부록: 왜 별도 구성이 아닌가 (사용자 우려 직답)
"초급용 별도 구성 = 일관 파이프라인 깨짐" 은 정확한 우려다. 본 설계는 별도 구성을 **거부**한다:
graph/synthesis/QA/verification/calibration 전부 동일 경로. 추가되는 것은 (a) 입력 노브 하나,
(b) 그 노브가 켜는 프롬프트 조건부 섹션(=`composition_mode` 가 이미 하는 것과 동형), (c) 코드
분류 맵. 새 그래프·새 노드·새 검증경로 **0**. "one engine, knobs" 패턴의 직접 연장이다.

---

## 구현 노트 (E1+E2 완료, 2026-06-25)

설계를 구현하며 두 가지가 정제·추가됐다.

### 정제 1 — 난이도는 입력이 아니라 seed 에서 파생
§2 의 `difficulty_target` 입력 노브 대신 **`is_basic(seed)` 파생**으로 단순화. 난이도를
별도 입력으로 받으면 (a) request/state/CLI 스레딩 추가 (b) `easy+dijkstra` 같은 모순 상태
가능. seed 가 곧 난이도를 말하면(basic 카테고리=easy) **단일소스·모순 불가·플러밍 0**.
`ipe/v1/schema/problem_spec.py` 에 `is_basic()`+`_BASIC_TARGETS`(단일소스), strategist/
formalizer/qa_reviewer 가 `is_basic(state.seed_algorithm)` 로 분기(비-basic byte-identical).

### 정제 2 — is_basic-aware difficulty charter (키스톤 레버)
원 RFC R1 은 "상수출력만 difficulty 게이트가 막는다"고 봤으나 **실측 반증**: 표준 charter 의
"trivial 하게 풀리거나" 가 입문 문제를 *쉽다는 이유로* reject(단순 곱셈·분기 하나). →
`qa_reviewer._DIFFICULTY_CHARTER_EASY` 추가: is_basic 문제는 단순함을 통과시키고 **진짜
퇴화(상수출력·모순)만** blocker. RFC 의 "난이도-agnostic" 의도를 코드화. 다른 QA kind·
알고리즘 문제 무영향.

### 측정 (P1 N=3, 완화 전후)
| 카테고리 | 완화 전 | 완화 후 |
|---|---|---|
| arithmetic | 1/3 | **3/3** |
| basic_io | 0/3 | **3/3** |
| conditional | 1/3 | **3/3** |
| loop_accumulate(배열) | 0/3 | 0/3 |
| **전체** | 2/12 (16%) | **9/12 (75%)** |

**스칼라 초급 = 9/9 (100%)**. 출하분 전부 깔끔한 Bronze(계좌 잔액·합격판정·성적표 등).

### A↔B 결합 (확증)
`loop_accumulate`(배열) 0/3 은 전부 **"N=0 ↔ constraints N∈[1,100]" 모순** = binary_search/
lis 를 깨뜨린 sequence write-side 갭. formalizer easy 프롬프트("N=0 지어내지 마")로도 안
막힘 → 모순이 하류(narrative/input_format 렌더)에서 발생. **배열 기반 초급은 B(N=0
단일소스화) 선행 필수.** 스칼라 초급은 A 로 완결, 배열은 B 후 자연 출하.

### 게이트 / 미완
- **게이트**: 890 passed / mypy --strict 100 / ruff green.
- **E3** 큐/스택(operation_sequence/int_array) — B 선행 권장(배열 N=0 공유).
- **E4** 난이도 calibration — 출하분 Bronze 안착 확인(적재 시 backfill).
