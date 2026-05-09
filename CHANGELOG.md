# Changelog

[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 형식.
모든 P4 changelist 와 1:1 대응. 외부 노출명은 v0.1 부터 `whooing-mcp-server-wrapper`.

## Unreleased

* (P3 항목 — IMAP 메일 폴링 / 후잉 webhook 수신 / Telegram 예산 알람 — 사용자
  결정으로 진행 안 함.)

## Unreleased — 2026-05-09 (formal PDF import tool)

### Added

* `src/whooing_mcp/tools/pdf_import.py` — 정식 도구 `whooing_import_pdf_statement`.
  - PDF 파싱 (pdf_adapters) + dedup (paginated client.list_entries) +
    auto-categorize (suggest_category) + 공식 MCP entries-create 통한 안전 insert
    + statement_import_log tracking + dry_run safety default + confirm_insert 가드.
  - CL 50708 의 일회용 스크립트 (`tests/_pdf_import_2026_04.py`) 일반화.
* `src/whooing_mcp/client.py` — `list_accounts(section_id)` + `flatten_accounts()`
  헬퍼. PDF import 의 account_id ↔ type/name 매핑용.

### Tools after this release

20개 — 신규 `whooing_import_pdf_statement` 추가.

## Unreleased — 2026-05-09 (delete via official MCP)

### Added

* `src/whooing_mcp/official_mcp.py` — 공식 후잉 MCP (`whooing.com/mcp`) HTTP
  JSON-RPC 클라이언트. `OfficialMcpClient.list_tools()` / `call_tool()`.
  서버는 stateless 라 init/initialized handshake 없이 단일 request 로 호출
  가능 (probe 검증 2026-05-09).
* 도구 `whooing_delete_entries` (총 19개) — 공식 MCP 의 `entries-delete` 를
  chained-call. confirm=True 가드 + statement_import_log 자동 동기화. 본
  wrapper 의 read-only 정책 부분 예외 (직접 REST X, 공식 MCP 위임).

### Why
2026-05-09 PDF import 작업 중 후잉 REST DELETE 가 우리 호출에 응답 안 함이
확인됐고, 사용자가 수동 cleanup. 본 CL 은 그 과정을 자동화 — 공식 MCP 의
entries-delete (검증된 호출 형식) 를 wrapper 가 대신 호출.

### Verified (live)
* 존재하지 않는 entry_id → official MCP isError 응답 정상 capture.
* test section 에 entry 임시 입력 → 새 도구로 삭제 → success.

## Unreleased — 2026-05-09 (PDF import session)

### Added

* `src/whooing_mcp/queue.py` SCHEMA_VERSION 2 → 3: `statement_import_log`
  테이블 신규. 카드명세서 PDF/CSV import 시 어느 파일/라인 → 어느 후잉
  entry_id 로 들어갔는지 audit trail. 향후 정식 import 도구 (deferred)
  의 backing store.
* `tests/_pdf_import_2026_04.py` — 2026-04 하나카드 명세서 import
  일회용 스크립트. 향후 정식 `whooing_import_pdf_statement` 도구의 reference.
* `tests/_probe_s9046.py` — accounts/entries 구조 탐색 helper.

### Fixed (Critical)

* `client.list_entries` — 후잉 server-side 100-cap 발견 → bisection
  pagination 구현. 이전엔 `limit=1000` 줘도 100 만 반환되어, 한 달치 거래
  > 100인 활발한 ledger 에서 누락 발생. 본 fix 후 122건 등 정상 반환.
* (직전 git c297054 의 `entries`→`rows` fix 와 함께 확인된 버그.)

### Known issues / TODO

* 후잉 REST API 의 DELETE entry 호출 형식 미확정 — 5+ 패턴 시도 모두
  실패 ("section_id parameter is required" 또는 "Unknown method").
  현재 wrapper 는 read-only 라 영향 X, 사용자가 공식 MCP 또는 web UI
  로 처리. 향후 정식 도구화 시 후잉 운영자에게 문의 필요.

## v0.1.7 — 2026-05-09

옵션 설정 파일 layer 도입.

### Added

* `src/whooing_mcp/config.py` — TOML 기반 옵션 파일 로더. tomllib (Python
  3.11+) 사용. 탐색 우선순위: `$WHOOING_CONFIG` → `<project>/whooing-mcp.toml`
  → `~/.config/whooing-mcp/config.toml`. 캐시 + force_reload 지원.
* `whooing-mcp.toml.example` — 모든 옵션 default OFF + 사용법 주석.
  GitHub 에 공개.
* `whooing-mcp.toml` — 사용자 본인 config. `.gitignore` 차단으로 GitHub 미반영.

### Changed

* `p4_sync.sync_db_to_p4()` — config 의 `[p4_sync] enabled` 검사 추가.
  false 면 즉시 silent skip. default OFF — GitHub clone 사용자가 무심코
  P4 명령 실패 안 보도록.

### Documentation

* DESIGN §8.4 신규 (옵션 설정 파일).
* README §3.5 신규 (whooing-mcp.toml 옵션 안내).

## v0.1.6 — 2026-05-09 (docs)

### Documentation

* README 의 첫 페이지 아키텍처 다이어그램을 ASCII → Mermaid flowchart 로
  교체. 한글 폭 차이로 깨져 보이던 정렬 문제 해결.
* DESIGN.md §6.7 의 역방향 조회 흐름 다이어그램 → Mermaid sequenceDiagram.
* README 도구 표 5 → 18 업데이트, annotation 5 도구 + PDF 2 도구 + monthly_close
  명시. 워크플로우 시나리오 4번째 (메모/태그) 추가. 도구 reference
  섹션에 5 도구 상세 명세.
* DESIGN.md §6.7 신규 (Local entry annotations 5 도구 명세). §10 캐싱
  표에 영구 저장 layer 추가. §14 향후확장 표를 현재 상태로 갱신
  (annotation/queue/category/monthly_close ✅ 완료 표시).
* CHANGELOG 의 v0.1.4 entry 의 도구 목록 명확화.

## v0.1.5 — 2026-05-09

SQLite db 정책 정착.

### Changed (Breaking)

* SQLite db default 위치: `~/.local/share/whooing-mcp/queue.db` →
  `<project root>/whooing-data.sqlite`. cross-machine continuity 를 위해
  P4 와 자동 연동되는 위치로 이동. `$WHOOING_QUEUE_PATH` override 우선순위는
  유지.

### Added

* `src/whooing_mcp/p4_sync.py` — db 변경 후 자동 P4 sync.
  * 새 numbered CL 생성 (default 사용 X)
  * description 에 무엇이 변경됐는지 (action_summary + p4 diff -ds 요약)
  * GitHub 으로는 가지 않음 (.gitignore 가 *.sqlite 차단)
  * 실패는 silent — 도구 응답의 'p4_sync' 필드로 가시화
* 모든 modifying 도구 (enqueue/confirm/dismiss_pending,
  set/remove_entry_note) 가 작업 끝에 sync_db_to_p4() 자동 호출.
* .gitignore 강화 — `whooing-data.sqlite*` + `*.db-journal/wal/shm`
  명시 차단.

### v0.1.4 — Local entry annotations

(직전 release — git 8f5d400, P4 50678) — 5 도구 + 자동 audit 부착.

## v0.1.3 — 2026-05-09

P2 월말 정산 합성 도구.

### Added

* `whooing_monthly_close` — DESIGN v2 §14 P2 항목. 한 호출로:
  * 거시 통계 (entries_count, total_money_sum, by_l_account top 10)
  * audit (memo `[ai]` 마커)
  * find_duplicates (월 범위)
  * reconcile_csv 또는 reconcile_pdf (선택 — csv_path/pdf_path 인자)
  * next_actions 가이드 배열 (한글 권고)

  inputs: yyyymm (YYYYMM), section_id?, csv_path?, pdf_path?,
  statement_issuer="auto", duplicate_tolerance_days=1,
  duplicate_min_similarity=0.85, audit_marker="[ai]"

### Tools after this release

13개 — 신규 monthly_close 추가. 기존 12개 변동 없음.

## v0.1.2 — 2026-05-09

P1 CSV / PDF 확장.

### Added

* CSV adapters: `hyundai_card`, `samsung_card` (CL 50668).
* **PDF 임포트 지원** — pdfplumber 기반:
  * `pdf_adapters/{shinhan,hyundai}_card.py` — 텍스트 추출 가능 PDF 만 지원.
  * 도구 2종: `whooing_reconcile_pdf`, `whooing_pdf_format_detect`.
* CSV detect: 첫 metadata 행(제목 등)을 스킵하고 진짜 header 행 자동 발견
  (`find_header_row` 헬퍼). 카드사명이 metadata 에 있으면 issuer 추정 보너스.
* dev dep: `reportlab` (fixture 재생성 시만 필요).
* runtime dep: `pdfplumber>=0.11`.

### Test fixtures

* `tests/fixtures/csv/{hyundai,samsung}_sample.csv` (synthetic, 제목 행 포함)
* `tests/fixtures/pdf/{shinhan,hyundai}_sample.pdf` (synthetic, reportlab
  생성 — `tests/_make_pdf_fixture.py` 가 재생성)

### Tools after this release

12개 — audit, parse_payment_sms, find_duplicates, reconcile_csv,
csv_format_detect, **reconcile_pdf**, **pdf_format_detect**,
suggest_category, enqueue_pending, list_pending, confirm_pending,
dismiss_pending.

### Limitations

* 텍스트 추출 가능 PDF 만 지원 (이미지/스캔 PDF 는 OCR 필요 — deferred).
* 비밀번호 PDF 미지원 (지원 시 ToolError 명확화 — 향후 보강 가능).

## v0.1.1 — 2026-05-09

* P1 SMS issuer 5종 추가 — `hyundai_card`, `samsung_card`, `toss`, `kakaobank`,
  `woori_bank`. 기존 신한/국민과 동일 패턴 (CL 50667).
* `tests/fixtures/sms/<issuer>_*.txt` 합성 샘플 8개 추가.

## v0.1.0 — 2026-05-09

후잉 가계부의 공식 MCP 서버(`whooing.com/mcp`) 위에서 동작하는 wrapper MCP
서버 첫 안정 버전. 도구 10개 / 테스트 140개.

### Added (도구 10)

* `whooing_audit_recent_ai_entries` — memo 마커 기반 LLM 입력 거래 추적
* `whooing_find_duplicates` — 같은 금액 + 유사 item + ±N일 거래쌍 후보
* `whooing_parse_payment_sms` — SMS/Push 결제 알림 → 후잉 항목 dict
  (지원: 신한카드, 국민카드)
* `whooing_reconcile_csv` — 카드사 명세서 CSV ↔ 후잉 entries 매칭
  (지원: 신한카드, 국민카드)
* `whooing_csv_format_detect` — CSV 헤더로 카드사 자동 탐지 (디버깅)
* `whooing_suggest_category` — 과거 거래 학습 → 새 가맹점의 l_account 추천
* `whooing_enqueue_pending` — SMS/메일/텍스트를 로컬 SQLite 큐에 저장
* `whooing_list_pending` — 큐 조회 (source/since 필터)
* `whooing_confirm_pending` — 후잉 입력 완료 → 큐 삭제 (의미적)
* `whooing_dismiss_pending` — 입력 안 함 → 큐 삭제 (의미적)

### Added (인프라)

* `bin/whooing-mcp-remote.sh` — 공식 MCP 등록 시 .env 의 토큰을 mcp-remote 로
  자동 전달하는 wrapper 스크립트 (CL 50657)
* `errors.py` — HTTP → ToolError 매핑 + per-section secret sanitize
  (CL 50666)
* client-side rate limit (분당 20 cap, 429 시 exponential backoff;
  DESIGN §9.2)
* 부트스트랩 토큰 sanity check (`__eyJh` prefix + 길이 50+; 위반 시 경고)
* 4단계 `.env` 자동 탐색 ($WHOOING_MCP_ENV → cwd → project root → ~/.config)

### Changed

* DESIGN.md v1 → v2: 자체 12 도구 구현 → 공식 MCP wrapper 모델로 전면 재작성
  (CL 50638). v1 폐기 사유는 §0 변경 이력 + §3.1 참조.
* 외부 노출명 통일: GitHub repo / pyproject `name` / FastMCP self-name 모두
  `whooing-mcp-server-wrapper` (CL 50665).
* 모든 사용방법을 `.env` 한 곳 기반으로 통일 — `-e` env block / `--header
  X-API-Key:` 인자 형태 제거 (CL 50657).

### Fixed

* `whooing_reconcile_csv` 가 빈 CSV 라도 user 가 start/end 명시 시 entries
  fetch 해서 extra 보고 (CL 50655).
* `whooing_reconcile_csv` 가 후잉 entries fetch 범위를 tolerance_days 만큼
  양쪽 확장 (경계 거래 매칭 누락 방지, CL 50655).

### Verified (CL #1 live smoke)

* 후잉 `/sections.json` 응답 shape 확정 (memory: whooing-api-truth.md §8).
* `webhook_token` per-section secret 발견 → CL #9 errors.py 의
  `SECRET_KEYS` 에 등록.
* `/entries.json` 응답 shape 는 테스트 섹션이 비어있어 미확정 — 첫 실 거래
  누적 후 검증 예정.

### P4 / Git correspondence

| P4 CL | git commit | 내용 |
|---|---|---|
| 50633 | 8e7702b | DESIGN.md v1 (자체 구현 안 — 폐기) |
| 50634 | (n/a)   | LICENSE / .gitignore / .claude/settings.local.json 동기화 |
| 50638 | cf19d4a | DESIGN.md v2 (wrapper 모델 — 채택) |
| 50639 | (n/a)   | .env (P4 only) |
| 50644 | 91ac4b8 | CL #1 — 골격 + audit |
| 50645 | (n/a)   | .env SECTION_ID (P4 only) |
| 50653 | ab60643 | CL #2 — find_duplicates |
| 50654 | 59c4e27 | CL #3 — parse_payment_sms (신한/국민) |
| 50655 | 51b4145 | CL #4 — reconcile_csv (신한/국민) |
| 50656 | df175f7 | README 종합 재작성 |
| 50657 | 389f034 | 모든 사용방법 .env 기반 통일 |
| 50658 | 833e1a2 | 자동 카테고리 학습 (suggest_category) |
| 50660 | 0207d00 | 자동입력 대기열 (4 도구) |
| 50665 | 5c4787c | rename → whooing-mcp-server-wrapper |
| 50666 | (이번)  | 견고성 / 배포 (errors.py + rate limit + sanity + CHANGELOG) |
