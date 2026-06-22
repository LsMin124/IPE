# 문제 은행 DB 접근 인수인계 (서비스 백엔드용)

파이프라인이 생성한 문제를 **공유 PostgreSQL**(Fly Managed Postgres)에 적재한다. 서비스
백엔드는 이 DB를 **읽기**로 사용한다. 이 문서는 백엔드 개발자가 **로컬에서** DB에 접속해
연동을 개발/테스트하는 방법 + 스키마 + 쿼리 예시를 담는다.

> 토폴로지: ① 파이프라인 서버(write) → ② **공유 DB**(이 문서) → ③ 서비스 백엔드(read).
> 컬럼 의미·패키지 매핑의 SSOT는 [`pipeline-service-api-contract.md`](./pipeline-service-api-contract.md) §2.5/§3.

---

## 0. 클러스터 정보

| 항목 | 값 |
|---|---|
| 종류 | Fly Managed Postgres (MPG) |
| 클러스터 이름 | `ipe-problem-bank-db` |
| 클러스터 ID | `w867508gn9nr3pk4` |
| 리전 | `nrt` (Tokyo) |
| 엔진 | PostgreSQL 16 |
| DB 이름 | `fly-db` |
| 내부 호스트 | `pgbouncer.w867508gn9nr3pk4.flympg.net:5432` (Fly 사설망 전용) |

⚠️ **`.flympg.net` 호스트는 Fly 사설 네트워크(6PN)에서만 DNS가 풀린다.** 로컬/CI 등
외부에서 접속하려면 아래 **프록시 터널**이 필요하다. (백엔드를 같은 Fly 조직에 배포하면
사설 호스트로 직접 접속 — 프록시 불요.)

---

## 1. 사전 준비 (1회)

### 1-1. flyctl 설치
```bash
# macOS
brew install flyctl
# Linux/기타
curl -L https://fly.io/install.sh | sh
```

### 1-2. Fly 접근 권한
이 DB는 조직 `personal`(서울_15반_이승민) 소유다. 백엔드 개발자는 둘 중 하나:
- **(권장) 조직 초대받기** — 클러스터 소유자가 `fly orgs invite <email>` 실행 → 초대 수락 후
  `fly auth login`.
- **공유 토큰 사용** — 소유자가 발급한 토큰을 환경변수로:
  ```bash
  export FLY_API_TOKEN='fo1_...'   # 소유자에게 보안 채널로 전달받음
  ```
  (스코프 좁힌 토큰: 소유자가 `fly tokens create org -o personal` 로 발급)

---

## 2. 프록시 터널 + 로컬 접속

```bash
# 터널: 로컬 16380 → 클러스터 5432 (이 터미널은 켜둔 채 유지)
fly mpg proxy w867508gn9nr3pk4 -p 16380
```

다른 터미널에서 `127.0.0.1:16380` 으로 접속한다.

```bash
# psql 로 확인 (PASSWORD 는 보안 채널로 전달받은 값)
psql "postgresql://<DB_USER>:<PASSWORD>@127.0.0.1:16380/fly-db"
```

연결 문자열 (드라이버별):

| 용도 | 문자열 |
|---|---|
| psql / libpq | `postgresql://<DB_USER>:<PASSWORD>@127.0.0.1:16380/fly-db` |
| Python psycopg3 / SQLAlchemy | `postgresql+psycopg://<DB_USER>:<PASSWORD>@127.0.0.1:16380/fly-db` |
| JDBC (Spring) | `jdbc:postgresql://127.0.0.1:16380/fly-db` (user/password 분리 설정) |

> 자격증명(`<DB_USER>`/`<PASSWORD>`)은 git에 커밋하지 않는다. 소유자가 보안 채널로 전달.
> 클러스터 기본 유저 연결 문자열은 소유자가 `fly mpg status w867508gn9nr3pk4` 로 확인 가능.
> **백엔드는 읽기만 하므로 read-only 유저 사용을 권장** (§5).

---

## 3. 스키마 (4 테이블)

파이프라인이 alembic으로 소유/관리한다(`ipe/v2/db/`). 백엔드는 **읽기 전용으로 가정**하고
DDL을 변경하지 않는다. 현재 적용 버전: `0005_problem_number` (head).

```sql
CREATE TABLE problems (
    id                VARCHAR(36) PRIMARY KEY,           -- uuid4 (hex-dash) — 내부 안정 식별
    problem_number    BIGINT      NOT NULL UNIQUE,       -- 공개 검색 번호(1000~). ✅ 노출 가능 — 사람이 쓰는 핸들(BOJ 문제번호 격), 적재 시 채번
    title             TEXT        NOT NULL,
    description       TEXT        NOT NULL,              -- 응시자에게 보일 지문(은닉 렌더 포함)
    input_format      TEXT        NOT NULL,              -- 입력 형식 명세
    output_format     TEXT        NOT NULL,              -- 출력 형식 명세
    constraints       JSONB       NOT NULL,              -- [{name,min_value,max_value,description}]
    samples           JSONB       NOT NULL,              -- [{input_text,expected_output,description}]
    internal_meta     JSONB       NOT NULL,              -- ⚠️ 내부 전용(응시자 비노출): hidden_algorithm/composition/qa 등
    difficulty        VARCHAR(64) NULL,                  -- BOJ 티어 라벨(예 'Gold IV'). meta.difficulty.label 승격. 옵션(--with-difficulty 켠 경우만 채워짐) (mig 0003, indexed)
    solution_code     TEXT        NULL,                  -- ⚠️ 내부 정해코드(응시자 비노출)
    solution_language VARCHAR(16) NULL,                  -- 예: 'python'
    status            VARCHAR(16) NOT NULL,              -- 'draft' | 'review' | 'published'
    time_limit_ms     INTEGER     NULL,                  -- 권장 시간제한(ms)
    created_at        TIMESTAMPTZ NOT NULL
);

CREATE TABLE test_cases (
    id         SERIAL PRIMARY KEY,
    problem_id VARCHAR(36) NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    seq        INTEGER     NOT NULL,                     -- 케이스 순번(0-base)
    input      TEXT        NOT NULL,
    expected   TEXT        NOT NULL,                     -- "" 가능(퇴화 케이스의 정해 출력이 빈값)
    category   VARCHAR(64) NULL                          -- 분포 진단용 tier/edge 이름 (예 'scale:small'). 채점 무관
);
CREATE INDEX ix_test_cases_problem_id ON test_cases (problem_id);

CREATE TABLE problem_algorithms (                        -- 문제 ↔ 알고리즘 분류 N:M (알고리즘 필터링용)
    problem_id VARCHAR(36) NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    algorithm  VARCHAR(64) NOT NULL,                     -- TargetAlgorithm 값(예 'dijkstra', 19종)
    role       VARCHAR(16) NOT NULL,                     -- 'core'(은닉 코어) | 'composition'(합성 기법)
    PRIMARY KEY (problem_id, algorithm)                  -- ⚠️ 응시자 비노출 (내부 분류)
);
CREATE INDEX ix_problem_algorithms_algorithm ON problem_algorithms (algorithm);

CREATE TABLE generation_requests (                       -- 생성 감사 로그(백엔드는 보통 불필요)
    idempotency_key VARCHAR(64) PRIMARY KEY,
    seed            VARCHAR(64) NOT NULL,                -- 시드 알고리즘 hint
    mode            VARCHAR(16) NOT NULL,                -- 'p1' | 'p2' (계약 §2.1)
    job_id          VARCHAR(64) NULL,
    final_status    VARCHAR(32) NOT NULL,                -- 'success' | 'fail_qa' | 'fail_*'
    attempts        INTEGER     NOT NULL,
    raw_package     JSONB       NULL,                    -- 수신 패키지 원문(재적재/감사)
    problem_id      VARCHAR(36) NULL REFERENCES problems(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL
);
```

### 핵심 규약
- **식별자**: `id`(UUID)=내부 안정 참조 / `problem_number`(1000~)=공개 검색·노출용 핸들
  (적재 시 채번, 영구 불변). 사용자 검색/노출에는 `problem_number` 사용.
- **응시자 비노출**: `internal_meta`, `solution_code`(+`solution_language`),
  `problem_algorithms` 전체(은닉 코어+합성). 응시자 API로 절대 내보내지 말 것 — 채점/검수
  /내부 필터에서만 사용. `difficulty`(티어 라벨)는 노출 가능하나(백엔드 product 정책)
  `internal_meta.difficulty` 의 reasoning/factors 는 정해 단서가 될 수 있어 비노출.
- **status 생애주기**: 파이프라인은 `draft`로 적재. **`published` 승격은 백엔드 책임**
  (응시자에게는 `published`만 노출 권장).
- **채점**: `test_cases.input` → 응시자 코드 실행 → stdout과 `expected`를 **줄단위
  exact-match**로 비교(문제는 답 유일성[tie-break]이 보장됨). `expected`는 빈 문자열일 수
  있으니 NULL 처리하지 말 것.
- **시간제한**: `time_limit_ms`는 정해 실행시간 기반 권장값. 백엔드가 채점기 정책에 맞게
  배수/하한 조정 가능.

---

## 4. 읽기 쿼리 예시

```sql
-- 출하 가능(published) 문제 목록 (공개 번호 포함)
SELECT problem_number, id, title, time_limit_ms, created_at
FROM problems
WHERE status = 'published'
ORDER BY problem_number;

-- 공개 번호로 문제 조회 (사용자 검색)
SELECT problem_number, id, title, description, input_format, output_format,
       constraints, samples, time_limit_ms
FROM problems
WHERE problem_number = :problem_number AND status = 'published';

-- 응시자에게 줄 문제 본문 (내부 컬럼 제외)
SELECT problem_number, id, title, description, input_format, output_format, constraints, samples, time_limit_ms
FROM problems
WHERE id = :problem_id;

-- 채점용 테스트케이스 (순서대로)
SELECT seq, input, expected, category
FROM test_cases
WHERE problem_id = :problem_id
ORDER BY seq;

-- 알고리즘 분류 필터: 'segtree' 가 코어든 합성이든 포함된 published 문제
SELECT DISTINCT p.id, p.title
FROM problems p
JOIN problem_algorithms pa ON pa.problem_id = p.id
WHERE pa.algorithm = 'segtree' AND p.status = 'published'
ORDER BY p.title;

-- 한 문제의 알고리즘 구성 (코어 + 합성)
SELECT algorithm, role FROM problem_algorithms WHERE problem_id = :problem_id;

-- 내부 검수용: 정해코드 + 숨은 알고리즘
SELECT solution_language, solution_code, internal_meta
FROM problems
WHERE id = :problem_id;
```

---

## 5. (권장) 읽기 전용 DB 유저

백엔드는 읽기만 하므로 SELECT 권한만 가진 유저를 별도로 쓰는 게 안전하다. 소유자가
프록시 접속 후 1회 실행:

```sql
CREATE ROLE backend_ro LOGIN PASSWORD '<강한_비밀번호>';
GRANT CONNECT ON DATABASE "fly-db" TO backend_ro;
GRANT USAGE ON SCHEMA public TO backend_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO backend_ro;
-- 향후 파이프라인이 추가할 테이블에도 자동 SELECT
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO backend_ro;
```

이후 백엔드는 `backend_ro` 자격증명으로 §2의 프록시를 통해 접속한다.

---

## 6. 트러블슈팅

- `failed to resolve host '...flympg.net'` → 프록시 미실행. §2의 `fly mpg proxy` 를 켠다.
- `no access token available` → `fly auth login` 또는 `FLY_API_TOKEN` 미설정(§1-2).
- psycopg에서 dialect 오류 → URL을 `postgresql+psycopg://` 로 (psycopg3 사용).
- 포트 충돌 → `fly mpg proxy ... -p <다른포트>` 로 변경.
