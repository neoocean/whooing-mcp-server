# whooing-mcp-server 설계 문서

이 문서는 [whooing.com](https://whooing.com) (이하 **후잉**) 가계부 서비스의 공개
API를 MCP(Model Context Protocol) 서버로 래핑해 Claude Code / Claude Desktop /
기타 MCP 호스트에서 자연어로 가계부를 조회·기록할 수 있게 하는 프로젝트의
설계를 정리한다.

> 본 문서는 **현재 시점의 스냅샷**이며, 코드와 어긋날 경우 항상 코드가 우선
> 한다. 큰 변경을 가하면서 본 문서가 더 이상 정확하지 않다고 판단되면, 같은
> 체인지리스트에서 이 파일을 함께 갱신하라.

> **작성 시점**: 2026-05-09. 구현 코드는 아직 없으며, 본 문서는 **착수 전
> 타당성 검토 + 설계 합의** 단계에서 작성되었다.

---

## 목차

| 절 | 제목 | 용도 |
|---|---|---|
| §1 | 목표 | 프로젝트 범위 한 페이지 요약 |
| §2 | 비-목표 (의도된 미구현) | "이거 왜 안 함?" 답 |
| §3 | 기술적 타당성 검토 (TL;DR) | "되긴 됨?" 답 |
| §4 | 후잉 공개 API 표면 정리 | 엔드포인트 / 인증 / 데이터 모델 |
| §5 | MCP 서버 아키텍처 | 모듈 / 트랜스포트 / 캐시 |
| §6 | 도구(tool) 목록 | 노출할 MCP 함수 명세 |
| §7 | 언어 / 런타임 선택 | Python vs TypeScript |
| §8 | 시크릿 관리 | `WHOOING_*` 환경변수 / `.env` |
| §9 | 에러 처리 + rate limit | 재시도 / 백오프 / 사용자 피드백 |
| §10 | 캐싱 전략 | 섹션 / 계정 / 빈번항목 |
| §11 | 테스트 전략 | 단위 / 모킹 / live smoke |
| §12 | 배포 | launchd / Claude Code 등록 |
| §13 | 보안·안전 가드 | 쓰기 도구 confirm / 재무 데이터 보호 |
| §14 | 향후 확장(deferred) | 합의됐지만 v1 미포함 |
| §15 | 참고 자료 + prior art | 출처 / 비교 |

---

## §1. 목표

자연어 한 마디로 후잉 가계부를 다음과 같이 다룰 수 있어야 한다.

- "어제 스타벅스에서 카드로 6,200원 썼어." → 분개 1건 자동 입력 (현금/카드 →
  외식)
- "이번 달 식비 얼마 썼어?" → P&L 카테고리별 합계 조회
- "5월 1일~7일 거래 내역 보여줘." → 항목 목록 조회
- "지금 자산/부채 상태?" → 재무상태표 조회
- "아직 입력 안 한 카드결제 문자 8건 한꺼번에 넣어줘." → 다건 일괄 입력

스코프:

1. **읽기 도구**: 섹션, 계정, 항목 목록, P&L, 재무상태표, 캘린더, 빈번/최근 항목.
2. **쓰기 도구**: 항목 추가(단건/다건), 수정, 삭제.
3. **단일 사용자**: 한 번에 한 사용자의 자격증명만 다룸 (멀티테넌시 X).
4. **stdio 트랜스포트 우선**, HTTP/SSE는 v1 옵션.

품질 기준:

- 평소 동작은 무인(unattended)이며 사용자 확인 없이 도구가 호출되어도 **안전한
  실패** (예: 계정명 모호 → 후보 반환, 임의 추측 X).
- API 키가 잘못되었을 때 **명확한 에러 메시지**를 LLM에 반환해야 사용자에게
  올바른 안내가 전파됨.
- 입력 도구는 **idempotency 키 또는 중복 검출**을 통해 같은 거래가 두 번
  들어가지 않도록 가드.

---

## §2. 비-목표 (의도된 미구현)

| 항목 | 안 하는 이유 |
|---|---|
| 후잉 계정 생성 / 회원가입 / 비밀번호 변경 | 공개 API에 없음. 사용자가 웹에서 직접. |
| **OAuth 3rd-party 인증 플로우** | 후잉 API는 사용자 발급 토큰 기반. 여러 사용자 권한 위임 시나리오 X. |
| **자동 카테고리 추천 ML 모델 자체 학습** | LLM이 자연어 → 계정 매핑을 충분히 함. 별도 학습 불필요. |
| 가계부 데이터 로컬 DB 미러링 | 후잉이 source of truth. 캐시는 메타데이터만. |
| 후잉 외 가계부 (뱅크샐러드/토스/Mint) 통합 | 별 프로젝트로. 본 서버는 후잉 전용. |
| 다중 후잉 사용자 동시 처리 | 단일 사용자 가정. MCP 호스트 인스턴스를 분리하면 됨. |
| 모바일 푸시 / SMS 결제문자 자동 파싱 | 후잉이 자체 웹훅 제공 (`whooing.com/info/webhook`). 본 서버는 LLM-driven 입력만. |
| 그래프/차트 렌더링 | MCP는 텍스트/구조화 데이터 반환. 시각화는 호스트 책임. |

---

## §3. 기술적 타당성 검토 (TL;DR)

**결론: 명백히 가능하다.** 다음 세 가지가 동시에 성립한다.

1. **공개 API가 존재하고 안정적**: 후잉은 사이트 푸터에 "API 문서" 링크와
   `whooing.com/forum/developer/ko/api_reference/user` 개발자 레퍼런스를 운영.
   2017년 블로그 포스트 (`lazyhansu`)에서 동일한 인증 스킴이 확인되며,
   2025년에도 같은 엔드포인트가 동작 중.
2. **MCP SDK 성숙**: `mcp` 파이썬 SDK / `@modelcontextprotocol/sdk` 타입스크립트
   SDK 모두 1.x 안정 버전. 도구(tool) 정의 → JSON Schema → stdio 트랜스포트
   까지 표준 패턴이 확립되어 있다.
3. **선행 구현 존재**: `jmjeong/whooing-mcp` (TypeScript, 16+ 도구, MIT)가
   실제로 동작하는 reference. 이는 본 프로젝트가 0→1 이 아니라 **품질·언어·
   운영 통합을 다시 잡는 1→1.x** 수준의 위험도임을 의미.

남은 불확실성 (구현 1주차에 검증할 항목):

- [ ] **rate limit**: 공식 문서에 명시된 분당/일당 한도가 있는지. 없으면 클라
      이언트 측 보수적 백오프(예: 초당 5건 cap) 적용.
- [ ] **signature 계산 방식**: 정적 발급값인지 (앱 등록 시 받은 그대로 사용),
      아니면 `nonce + timestamp + body`로 매번 HMAC인지. 선행 구현은 정적값을
      그대로 헤더에 박는 듯 보이나 직접 한 번 호출해서 확정.
- [ ] **응답 인코딩 (한글)**: `item` 필드 한글이 URL 인코딩만 필요한지, 본문
      JSON에서는 그대로 UTF-8로 가는지.
- [ ] **시간대**: `entry_date`는 KST 기준 YYYYMMDD. 서버 시간대가 다를 경우의
      변환 책임을 어디에 둘지 (→ §4.4).
- [ ] **delete의 멱등성**: 이미 삭제된 entry_id에 대한 두 번째 DELETE 응답이
      404인지 200인지.

위 5가지는 **DESIGN을 막는 항목이 아니며**, 구현 첫 PR(=changelist)에서
실제 호출 1회로 결정 가능. 따라서 본 프로젝트는 진행 가능.

---

## §4. 후잉 공개 API 표면 정리

> ⚠️ 본 절의 일부 디테일은 공개 블로그 + 선행 구현 + 사이트 푸터 링크에서
> 역추적한 것이다. 구현 시 반드시 공식 `apidoc` 응답으로 한 번 더 검증할 것.
> 변경되면 본 절을 같은 changelist에서 갱신.

### §4.1 인증

모든 요청은 `X-API-KEY` 헤더에 다음 5개 값을 담는다.

| 키 | 출처 | 비고 |
|---|---|---|
| `app_id` | 후잉 앱 등록 시 발급 | 영구 |
| `token` | 사용자별 발급 토큰 | 영구 (사용자가 revoke 가능) |
| `signature` | 앱 등록 시 발급 | 영구 |
| `nonce` | 클라이언트가 매 요청 생성 | 16바이트 hex 권장 |
| `timestamp` | Unix epoch (초) | 서버 시각과 ±5분 이내 가정 |

**헤더 직렬화** (잠정 — 공식 문서 확인 필요):

```
X-API-KEY: app_id=ABC; token=XYZ; signature=DEF; nonce=...; timestamp=...
```

선행 구현이 정적 `signature`를 그대로 보내는 것으로 보이므로, 별도 HMAC
계산은 v1에서 가정하지 않는다. (만약 HMAC이라면 `hmac.new(secret, msg,
sha256)`로 추가 1줄.)

### §4.2 엔드포인트 (확인된 + 선행 구현 기반 추정)

베이스: `https://whooing.com/api`

| 카테고리 | Method | 경로 | 용도 | 상태 |
|---|---|---|---|---|
| 섹션 | GET | `/sections.json_array` | 사용자가 가진 가계부(섹션) 목록 | ✓ 확인 |
| 계정 | GET | `/accounts.json_array?section_id=…` | 한 섹션의 계정 목록 (자산/부채/자본/수익/비용) | ✓ 확인 |
| 항목 | GET | `/entries.json?section_id=…&start_date=…&end_date=…` | 거래 항목 조회 (1년 이내) | ✓ 확인 |
| 항목 | POST | `/entries.json_array` | 거래 항목 입력 | ✓ 확인 |
| 항목 | PUT/POST | `/entries/{entry_id}.json` | 거래 항목 수정 | 추정 |
| 항목 | DELETE | `/entries/{entry_id}.json` | 거래 항목 삭제 | 추정 |
| 리포트 | GET | `/report_summary.json?…` | P&L 요약 | 추정 |
| 캘린더 | GET | `/calendar.json?…&yyyymm=…` | 일별 수입/지출 | 추정 |
| 예산 | GET | `/budget.json?…` | 예산 대비 실적 | 추정 |
| 빈번 | GET | `/frequent_items.json?…` | 자주 쓰는 거래 템플릿 | 추정 |

**1년 제약**: `entries.json`의 date range는 **최대 1년**. 그 이상 조회는
클라이언트 측에서 분할 + 병합.

### §4.3 데이터 모델 — 복식부기

후잉의 가장 큰 특징은 **복식부기**. 모든 항목은 차변(left) / 대변(right)
계정과 금액으로 표현된다.

```
{
  "section_id": "s86473",
  "entry_date": "20260509",
  "l_account_type": "expenses",   "l_account_id": "e0007",  "l_account": "외식",
  "r_account_type": "assets",     "r_account_id": "a0002",  "r_account": "신한카드",
  "money": 6200,
  "item": "스타벅스 아메리카노",
  "memo": ""
}
```

| 거래 종류 | 차변 (l_account) | 대변 (r_account) |
|---|---|---|
| 카드/현금으로 지출 | 비용 (외식, 교통…) | 자산 (현금) 또는 부채 (카드대금) |
| 월급 입금 | 자산 (은행) | 수익 (월급) |
| 자산 간 이체 | 자산 (도착 계좌) | 자산 (출발 계좌) |
| 카드 대금 결제 | 부채 (카드대금) | 자산 (은행) |

**MCP 도구 입장**: 사용자는 자연어로 "스벅 6200원 신한카드"라고만 말한다.
LLM이 → 계정 매핑을 추론 → `whooing_add_entry(item="스타벅스 아메리카노",
money=6200, l="외식", r="신한카드")` 호출 → 서버가 캐시된 계정명 → ID 변환.
**계정명 모호 시 후보 목록을 반환하고 입력은 거부**해야 LLM이 사용자에게
재질문할 수 있다.

### §4.4 시간대

후잉은 KST 운영 가정. `entry_date`는 KST 자정 기준 YYYYMMDD. MCP 서버는
다음 규칙을 따른다.

1. 사용자가 명시적으로 "2026-05-09"라고 주면 그대로 사용.
2. "어제", "오늘" 같은 상대 표현은 **MCP 서버 기준의 KST**로 해석. (서버
   호스트가 다른 시간대여도 `zoneinfo("Asia/Seoul")` 강제.)
3. LLM이 잘못된 형식(예: "2026/5/9")을 보내면 서버 측에서 정규화하되, 정규화
   결과를 응답에 포함해 LLM이 사용자에게 confirmable 하도록.

---

## §5. MCP 서버 아키텍처

### §5.1 트랜스포트

| 트랜스포트 | v1 | 비고 |
|---|---|---|
| **stdio** | ✓ | Claude Code / Claude Desktop 기본 |
| **HTTP/SSE** | △ | flag로 활성화. 데몬 모드 운영 시 |
| WebSocket | ✗ | MCP가 SSE로 충분 |

### §5.2 모듈 분할

```
whooing-mcp-server/
├── DESIGN.md                ← 본 문서
├── README.md                ← 사용자 quickstart
├── pyproject.toml           ← Python 패키지 메타
├── .env.example
├── src/
│   └── whooing_mcp/
│       ├── __init__.py
│       ├── __main__.py      ← `python -m whooing_mcp` 진입
│       ├── server.py        ← MCP server 인스턴스 + tool 등록
│       ├── client.py        ← 후잉 HTTP 클라이언트 (httpx)
│       ├── auth.py          ← X-API-KEY 헤더 빌드 + nonce/timestamp
│       ├── cache.py         ← 섹션/계정/빈번 인메모리 + TTL 디스크 캐시
│       ├── models.py        ← Pydantic: Section, Account, Entry, …
│       ├── tools/
│       │   ├── read.py      ← whooing_pl, whooing_entries, …
│       │   └── write.py     ← whooing_add_entry, …
│       ├── resolver.py      ← 계정명 → ID 매칭 (퍼지 + 모호성 처리)
│       ├── dates.py         ← KST 정규화, "어제/오늘" 파싱
│       └── errors.py        ← 후잉 에러 → MCP 에러 매핑
├── tests/
│   ├── test_auth.py
│   ├── test_resolver.py
│   ├── test_dates.py
│   ├── test_client_mock.py  ← respx 기반 HTTP 모킹
│   └── test_tools_e2e.py    ← live (env가 있으면 실행, 없으면 skip)
└── examples/
    └── claude_desktop_config.json
```

### §5.3 의존성 (잠정)

| 패키지 | 용도 |
|---|---|
| `mcp >= 1.0` | MCP 서버 SDK |
| `httpx` | 후잉 API HTTP 호출 (sync + async) |
| `pydantic >= 2` | 모델 정의, 도구 입력 스키마 자동 생성 |
| `python-dotenv` | `.env` 로딩 |
| `rapidfuzz` | 계정명 퍼지 매칭 |
| `tzdata` | Windows 환경 KST 타임존 (다른 OS는 system tzdata) |

테스트:

| 패키지 | 용도 |
|---|---|
| `pytest` | 러너 |
| `respx` | httpx 모킹 |
| `pytest-asyncio` | async 테스트 |

### §5.4 라이프사이클

```
[stdio 모드]
  Claude Code ──spawn──▶ python -m whooing_mcp
                              │
                              ▼
                  ┌───────────────────────┐
                  │ server.start()        │
                  │  ├─ 환경변수 로드      │
                  │  ├─ client = WhooingClient(...)
                  │  ├─ cache.bootstrap()  ← sections + accounts 1회 prefetch
                  │  └─ stdio_loop()       ← MCP framing
                  └───────────────────────┘
                              │
                       (도구 호출마다)
                              │
                              ▼
                       handle_tool(name, args)
```

**bootstrap 실패 정책**: 자격증명이 잘못된 경우 stdout에 MCP 에러를 보내고
정상 종료(exit 1). Claude Code는 다음 도구 호출 시 재시작.

---

## §6. 도구(tool) 목록

선행 구현(`jmjeong/whooing-mcp`)의 16개 도구를 baseline으로 하되, 본 서버는
**v1을 12개로 축소** 후 §14에서 점진 확장한다.

### §6.1 v1 도구 (12개)

#### 읽기 (8)

| 도구 | 입력 | 출력 | 비고 |
|---|---|---|---|
| `whooing_sections` | — | `[{id, name, default}]` | bootstrap 캐시에서 즉답 |
| `whooing_accounts` | `section_id?` | 계정 목록 (type별 그룹) | 캐시 |
| `whooing_entries` | `section_id?, start_date, end_date, account?, q?, limit?` | 항목 배열 | 1년 초과 시 분할 호출 |
| `whooing_entry_detail` | `entry_id` | 단일 항목 전체 필드 | |
| `whooing_pl` | `section_id?, start_date, end_date, group_by=category` | 카테고리별 수입/지출 합계 | |
| `whooing_balance` | `section_id?, as_of?` | 자산/부채/자본 스냅샷 | |
| `whooing_calendar` | `section_id?, yyyymm` | 일별 수입/지출 합계 | |
| `whooing_frequent_items` | `section_id?, limit?` | 자주 쓰는 거래 템플릿 | |

#### 쓰기 (4)

| 도구 | 입력 | 출력 | 가드 |
|---|---|---|---|
| `whooing_add_entry` | `section_id?, entry_date, l_account, r_account, money, item, memo?` | `{entry_id}` | 계정 모호 시 후보 반환 + 입력 거부 |
| `whooing_bulk_add_entries` | `entries: [...]` | `[{entry_id} \| {error}]` | 부분 성공 허용. 실패한 건은 LLM이 사용자에게 보고 |
| `whooing_update_entry` | `entry_id, …변경 필드…` | `{ok}` | 원본 fetch → diff 표시 |
| `whooing_delete_entry` | `entry_id, confirm=true` | `{ok}` | `confirm` 누락 시 entry detail만 반환 |

### §6.2 도구 명세 컨벤션

- **모든 입력은 Pydantic 모델**, JSON Schema 자동 생성 → MCP가 LLM에 전달.
- **출력은 항상 dict**, 절대 raw 문자열 X. (LLM이 구조 파싱 가능하도록.)
- **에러는 raise**, MCP가 자동으로 isError=true로 변환.
- **`section_id` 옵셔널**: 환경변수 `WHOOING_SECTION_ID`가 default. 사용자가
  여러 섹션을 가진 경우만 명시적으로 받음.

### §6.3 계정명 → ID 해석 (resolver.py)

쓰기 도구에서 `l_account="외식"` 같은 한글명이 들어오면:

1. 캐시된 계정 목록에서 **정확 일치** 탐색.
2. 없으면 `rapidfuzz.process.extract`로 ratio ≥ 90 후보 1개 탐색.
3. 없으면 ratio ≥ 70 후보 **목록**을 반환하면서 에러 raise (`AccountAmbiguous`).

이 가드가 있어야 LLM이 잘못된 계정에 무단 입력하지 않음.

---

## §7. 언어 / 런타임 선택

### 결정: **Python 3.11+**

| 기준 | Python | TypeScript |
|---|---|---|
| 워크스페이스 일관성 (`scripts/` 대부분 Python) | **+** | − |
| MCP SDK 성숙도 | + (1.x) | + (1.x) |
| 한글 처리 (URL 인코딩, 인코딩 디버깅) | + | + |
| Pydantic v2로 자동 JSON Schema | **+** | (zod로 가능) |
| launchd plist 통합 패턴 (워크스페이스에 이미 존재) | **+** | (가능하나 새 패턴) |
| 선행 구현 재사용 | − (TS) | + |

워크스페이스에 이미 `docker-monitor`, `arq-backup-tui`, `p4v-tui`, `rx`,
`upload_to_confluence` 등 Python 도구가 다수이고 launchd 운영 패턴도 정착되어
있으므로, **Python으로 통일하는 운영 단순성**이 선행 구현 재사용 이득보다 크다.

> 단, 선행 구현의 **API 호출 패턴, 에러 코드 매핑, 도구 명명 규칙**은 그대로
> 차용한다. 이는 향후 사용자가 두 구현 사이를 옮겨다닐 때 마찰을 줄인다.

---

## §8. 시크릿 관리

### §8.1 환경변수

| 키 | 필수 | 설명 |
|---|---|---|
| `WHOOING_APP_ID` | ✓ | 앱 등록 시 발급 |
| `WHOOING_TOKEN` | ✓ | 사용자 토큰 |
| `WHOOING_SIGNATURE` | ✓ | 앱 등록 시 발급 |
| `WHOOING_SECTION_ID` | △ | 섹션 1개만 쓰는 사용자의 default |
| `WHOOING_BASE_URL` | △ | 기본 `https://whooing.com/api`. staging 가정 X. |
| `WHOOING_HTTP_TIMEOUT` | △ | 기본 10초 |
| `WHOOING_LOG_LEVEL` | △ | `INFO` 기본 |

### §8.2 로딩 우선순위

1. 프로세스 환경변수 (Claude Code config의 `env`)
2. 워킹 디렉터리의 `.env`
3. `~/.config/whooing-mcp/.env`
4. **로깅 시 절대 출력 금지** (값을 mask. `auth.py`의 `__repr__`도 마스크.)

### §8.3 자격증명 회수 시나리오

사용자가 후잉 웹에서 토큰을 revoke 한 경우 → 401/403. 서버는:

1. 캐시 무효화 (잘못된 자격으로 캐시된 메타가 stale일 수 있음)
2. MCP 에러 메시지: "WHOOING_TOKEN이 거부되었습니다. 후잉 → 앱 설정에서 토큰을
   재발급하고 환경변수를 갱신하세요." (한글로 명확히)
3. 자동 재시도 X (영구적 에러로 취급).

---

## §9. 에러 처리 + rate limit

### §9.1 후잉 → MCP 에러 매핑

| HTTP | 후잉 의미(추정) | MCP 동작 |
|---|---|---|
| 200 | OK | 정상 반환 |
| 400 | 파라미터 오류 | `ToolError(USER_INPUT, …)` 즉시 반환 |
| 401/403 | 자격 거부 | `ToolError(AUTH, …)` + 캐시 무효화. 재시도 X |
| 404 | 리소스 없음 | 도구별 처리 (delete는 멱등 OK, 나머지는 에러) |
| 409 | 중복 entry | `whooing_add_entry`만 발생. 사용자에게 "유사한 항목이 이미 있음" + 후보 반환 |
| 429 | rate limit | exponential backoff (1s, 2s, 4s, 8s; max 4회). 그래도 실패 시 `ToolError(RATE_LIMIT, …)` |
| 5xx | 서버 오류 | 재시도 (1s, 3s; max 2회). 실패 시 `ToolError(UPSTREAM, …)` |

### §9.2 클라이언트 측 자체 throttle

공식 한도가 불명확하므로 보수적으로:

- **분당 60회 cap** (per process). 초과 시 client 측 큐잉.
- bulk add도 내부적으로는 1건씩 직렬화 (서버 측 트랜잭션 보장 없음).

### §9.3 idempotency 가드 (쓰기)

`whooing_add_entry`는 입력 시점에 다음 검사:

1. 같은 `(entry_date, money, item, l_account, r_account)` 조합이 이미 같은
   날짜 ±1일 안에 있으면 **자동 입력 거부**, 후보 entry_id를 반환하면서
   `Possible duplicate` 경고. LLM이 사용자에게 확인 받은 후 `force=true`로
   재호출.

---

## §10. 캐싱 전략

| 데이터 | 위치 | TTL | 갱신 트리거 |
|---|---|---|---|
| sections | 메모리 | 세션 전체 | bootstrap |
| accounts | 메모리 | 1시간 | `whooing_accounts` 호출 / 만료 / `cache.invalidate()` |
| frequent_items | 메모리 | 30분 | 호출 시 lazy |
| 어제까지의 entries | 메모리 LRU(50) | 30분 | 호출 |
| 오늘의 entries | 캐시 X | — | 매번 fresh (입력 직후 반영 필요) |

디스크 캐시는 v1에 없음. 프로세스 재시작 시 bootstrap 1회로 충분 (sections +
accounts ≈ 2 요청, < 1초).

---

## §11. 테스트 전략

### §11.1 레이어

| 레이어 | 도구 | 커버 대상 |
|---|---|---|
| 단위 | pytest | `auth.py`, `dates.py`, `resolver.py`, `cache.py` |
| HTTP 모킹 | respx | `client.py` (요청 빌드 + 응답 파싱) |
| 도구 단위 | pytest + 모킹 클라이언트 | `tools/*.py` 입출력 |
| live smoke | pytest + 환경변수 | 실 후잉 API. CI 기본 skip, `WHOOING_LIVE_TEST=1`이면 실행 |

### §11.2 live smoke 정책

- 별도 **테스트용 섹션**을 후잉에 만들어 사용 (실 가계부 오염 X).
- smoke 흐름: bootstrap → add_entry(테스트 거래) → entries 조회 → delete_entry
  → 정리 확인.
- 재무 데이터의 민감도 때문에 **CI runner에 자격증명을 두지 않는다**. 로컬
  개발자 머신에서만 수동 실행.

### §11.3 회귀 픽스처

후잉 응답 샘플(JSON)을 `tests/fixtures/`에 캡처해두고 모킹에 사용. 회귀
방지 + 사용자가 후잉 응답 형식 변경을 의심할 때 진단 자료.

---

## §12. 배포

### §12.1 로컬 / Claude Code 등록

`examples/claude_desktop_config.json` 발췌:

```json
{
  "mcpServers": {
    "whooing": {
      "command": "python",
      "args": ["-m", "whooing_mcp"],
      "env": {
        "WHOOING_APP_ID": "…",
        "WHOOING_TOKEN": "…",
        "WHOOING_SIGNATURE": "…",
        "WHOOING_SECTION_ID": "…"
      }
    }
  }
}
```

### §12.2 launchd (macOS, HTTP 모드)

데몬으로 띄워두고 여러 호스트에서 공유하고 싶을 때:

```
~/Library/LaunchAgents/com.whooing.mcp.plist
  → python -m whooing_mcp --http --port 8182
```

워크스페이스에 이미 launchd 패턴이 정착되어 있으므로 동일 컨벤션을 따른다.
HTTP 모드는 v1.1 이후로 미룬다 (§14).

### §12.3 패키징

- `pyproject.toml` (PEP 621) + `hatchling` 또는 `setuptools` 빌드 백엔드.
- `pip install -e .` 로 개발 모드.
- PyPI 배포는 v1 이후 결정.

---

## §13. 보안·안전 가드

재무 데이터를 다루므로 다른 도구보다 가드가 빡빡하다.

| 가드 | 적용 |
|---|---|
| 자격증명 절대 로깅 금지 | `auth.py.__repr__` 마스크, 디버그 로그도 헤더 마스크 |
| 쓰기 도구 idempotency | §9.3 |
| delete 명시적 confirm | §6.1 (delete는 `confirm=true` 없으면 detail만 반환) |
| 계정명 모호 시 입력 거부 | §6.3 |
| upstream 5xx 재시도 cap | §9.1 (무한 재시도 X) |
| bulk 부분 성공 명시 | 응답에 성공/실패 배열 분리 |
| 응답 페이로드 sample을 git에 커밋할 때 마스크 | 픽스처 작성 시 실 계좌번호 / 잔액 → 더미 |

### §13.1 멀티 클라이언트 가드

같은 자격증명으로 두 MCP 인스턴스가 동시에 쓰기 호출하면 race가 가능. v1에는
경고만:

- bootstrap 시 자체 PID + 시작 시각을 stderr에 출력.
- 운영자가 두 인스턴스를 띄운 것을 알아챌 수 있도록.

분산 락은 v2 이후.

---

## §14. 향후 확장 (deferred)

| 항목 | 우선순위 | 비고 |
|---|---|---|
| HTTP/SSE 트랜스포트 | P1 | 데몬 모드. launchd plist 포함 |
| `whooing_search_entries` (서버사이드 풀텍스트) | P1 | 후잉 API 지원 확인 필요 |
| `whooing_budget` (예산 대비 실적) | P2 | |
| `whooing_monthly_summary` | P2 | 캘린더 + P&L 합성 |
| `whooing_account_activity` (계정별 거래 내역) | P2 | |
| `whooing_duplicate_candidates` (잠재 중복 탐지) | P2 | idempotency 가드의 explicit 도구화 |
| 로컬 쓰기 작업 일지 (audit log) | P2 | "지난 주에 LLM이 입력한 거래 모두 보여줘" |
| 후잉 웹훅 수신 → MCP 호스트 푸시 (notification) | P3 | MCP 서버가 outbound notification 가능 |
| 다중 사용자 (자격증명 vault 통합) | P3 | |
| 분산 락 / 동시성 가드 | P3 | |
| TUI 동반 도구 | P3 | 워크스페이스의 다른 `*-tui` 패턴과 일관성 |

---

## §15. 참고 자료 + prior art

### 공식

- [whooing.com](https://whooing.com/) — 서비스 홈. 푸터에 "API 문서" 링크.
- [whooing.com/forum/developer/ko/api_reference/user](https://whooing.com/forum/developer/ko/api_reference/user)
  — 공개 개발자 API 레퍼런스 (구현 시 본문을 직접 확인).
- [whooing.com/info/webhook](https://whooing.com/info/webhook) — 웹훅 (본
  프로젝트 범위 외).
- [whooing.com/help/dic/accounts/ko](https://whooing.com/help/dic/accounts/ko)
  — 계정 용어 사전 (resolver.py 동의어 사전 시드용).

### 비공식 / 선행 구현

- [jmjeong/whooing-mcp](https://github.com/jmjeong/whooing-mcp) — TypeScript
  MCP 서버. 16+ 도구, MIT. **본 프로젝트의 1차 참조**.
- [glama.ai/mcp/servers/jmjeong/whooing-mcp](https://glama.ai/mcp/servers/jmjeong/whooing-mcp)
  — 위 구현의 메타데이터 페이지.
- [Lazy Hansu — Python으로 whooing 텔레그램 봇 만들기 #2 (2017)](https://lazyhansu.wordpress.com/2017/10/24/python-%ec%9c%bc%eb%a1%9c-whooing-%ed%85%94%eb%a0%88%ea%b7%b8%eb%9e%a8-%eb%b4%87-%eb%a7%8c%eb%93%a4%ea%b8%b0-2/)
  — `X-API-KEY` 헤더 5요소 스킴의 1차 출처.
- [XeO3/i-bought-it](https://github.com/XeO3/i-bought-it) — TypeScript /
  Vue 기반 간이 입력기. UX 참조.

### MCP

- [modelcontextprotocol.io](https://modelcontextprotocol.io/) — 사양.
- [github.com/modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk)
  — Python SDK.

---

## 부록 A. 첫 changelist 체크리스트

DESIGN 합의 후 첫 구현 changelist에서 처리할 항목 (순서대로):

- [ ] `pyproject.toml` + `src/whooing_mcp/__init__.py` 골격
- [ ] `auth.py` + `client.py` 최소 구현 + `whooing_sections`, `whooing_accounts`
      두 도구만 노출
- [ ] **실 API 1회 호출**로 §3의 5가지 미확정 항목 결론 → 본 문서 갱신
- [ ] `tests/test_client_mock.py` 픽스처 캡처
- [ ] `examples/claude_desktop_config.json`
- [ ] `README.md` 사용자 quickstart

이 첫 PR이 머지되면 §6의 나머지 도구를 PR 단위로 추가한다.
