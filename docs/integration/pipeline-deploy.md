# 파이프라인 API 서버 배포 가이드 (Slice 2)

계약: [pipeline-service-api-contract.md](pipeline-service-api-contract.md) (v2.0).
구현: `ipe/v2/api.py` (`create_app` 팩토리).

## 로컬 실행 (개발)

```bash
pip install -e ".[api]"
set -a; source .env; set +a          # ANTHROPIC_API_KEY + IPE_API_KEY
uvicorn 'ipe.v2.api:create_app' --factory --host 0.0.0.0 --port 8000
```

## 컨테이너 (배포)

```bash
docker build -f Dockerfile.api -t ipe-api:latest .
docker run --rm -p 8000:8000 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e IPE_API_KEY=<백엔드와 공유한 정적 키> \
  -e IPE_MAX_CONCURRENT_GENERATIONS=2 \
  ipe-api:latest
```

## 환경 변수

| 변수 | 필수 | 설명 |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | LLM 호출 (생성 비용 발생원) |
| `IPE_API_KEY` | ✅ | 백엔드↔파이프라인 정적 인증 (`X-API-Key`). 없으면 기동 거부 |
| `IPE_MAX_CONCURRENT_GENERATIONS` | — | 동시 생성 슬롯 (기본 2, 초과 429) |

## 운용 주의

- **단일 프로세스 전제**: job/idempotency 가 in-memory — uvicorn 워커 1
  (이미지 CMD 기본값), 수평 확장 시 단일 인스턴스 또는 sticky 라우팅.
  인스턴스 재시작 시 진행 중 job 은 유실되며 백엔드가 404 를 보고 새
  generate 로 재시도한다 (계약 §3).
- **sandbox tier**: 컨테이너 내부엔 docker 가 없어 자동으로 rlimit tier
  (리소스 제한 서브프로세스)로 동작 — 컨테이너 자체가 외부 격리 경계.
  호스트 실행 시엔 docker/sandbox-exec 가 자동 선택됨.
- **비용**: 1 run ≈ $0.4~0.6 실측 (계약 §5). 동시 슬롯 × 시간으로 상한 바운드.
- **타임아웃**: 1 run 2~6분 — LB/프록시의 idle timeout 은 무관 (poll 기반,
  개별 HTTP 요청은 짧음).

## smoke 절차 (배포 후 1회)

```bash
# 1) 헬스
curl -fsS http://HOST:8000/healthz

# 2) 생성 시작 (~$0.5, 2~6분)
curl -fsS -X POST http://HOST:8000/v1/problems/generate \
  -H "X-API-Key: $IPE_API_KEY" -H "Content-Type: application/json" \
  -d '{"mode":"p2","seed_algorithm":"dijkstra","idempotency_key":"smoke-1"}'

# 3) 폴링 (15~30s 간격) — completed + final_status 확인
curl -fsS http://HOST:8000/v1/jobs/<job_id> -H "X-API-Key: $IPE_API_KEY"
```

`final_status` 가 `success`/`fail_qa` 면 `package` 가 계약 §2.5 형상으로
오는지 확인. `fail_*` 도 valid 종료다 (계약 §2.3 — 재시도 대상일 뿐).
