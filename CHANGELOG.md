# Changelog

[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 형식.
모든 P4 changelist 와 1:1 대응. 외부 노출명은 v0.1 부터 `whooing-mcp-server-wrapper`.

## Unreleased

* (P3 항목 — IMAP 메일 폴링 / 후잉 webhook 수신 / Telegram 예산 알람 — 사용자
  결정으로 진행 안 함.)
* (어댑터 / 첨부 layer 추가 항목 — whooing-tui 의 monorepo `core/` 에서 진행)

## v0.2.0 — 2026-05-10

**Breaking** — wrapper 에서 user-facing flow (statement import / 첨부 / 메모/태그)
도구 10 개 제거. 해당 영역은 신규 monorepo
[whooing-tui](https://github.com/neoocean/whooing-tui) 가 가져감. wrapper 는 LLM
자동화 / audit / categorize / pending queue 에 집중.

### Removed (10 도구)

* `whooing_set_entry_note`, `whooing_get_entry_annotations`, `whooing_remove_entry_note`
* `whooing_list_hashtags`, `whooing_find_entries_by_hashtag`
* `whooing_import_html_statement`, `whooing_import_pdf_statement`
* `whooing_attach_file_to_entry`, `whooing_list_entry_attachments`, `whooing_remove_attachment`

→ 24 → **14 도구** (`whooing_audit_recent_ai_entries`, `whooing_parse_payment_sms`,
`whooing_find_duplicates`, `whooing_reconcile_csv`/`pdf`,
`whooing_csv_format_detect`, `whooing_pdf_format_detect`,
`whooing_suggest_category`, `whooing_enqueue_pending`/`list`/`confirm`/`dismiss`,
`whooing_delete_entries`, `whooing_monthly_close`).

### Changed

* **신규 환경변수 `WHOOING_DATA_DIR`** (default `~/.whooing/`) — whooing-tui 와
  같은 db / attachments 공유. 옛 `<project>/whooing-data.sqlite` 도 backward-compat
  (경고와 함께 동작). migration 도구 제공:
  `python tools/migrate-to-shared-data-dir.py [--confirm]`.
* **어댑터 (html / csv / pdf) 와 storage 가 whooing-core 로 이전.** wrapper 는
  `pip install whooing-core` (sibling monorepo `whooing-tui/core`) 로 가져옴.
  로컬 사본 / 중복 테스트 모두 제거.
* **annotation / hashtag / entry_attachments 테이블은 wrapper read-only.**
  `whooing_mcp.queue.open_db_ro()` 가 `mode=ro` URI 강제. wrapper write 시도 시
  `OperationalError`. graceful degradation — TUI 가 schema init 안 했어도 빈
  `local_annotations` 반환.
* `whooing_mcp.{annotations,attachments}.py` 는 read-only helper 만 (각 71 lines).
  CRUD 는 모두 whooing-core 또는 TUI 가 owner.

### Fixed

* (v0.1.12 의 P4 빈 CL leak 방지가 본 release 에 그대로 효력.)

### Migration

1. `pip install -e ../whooing-tui/core` (sibling) 또는 `pip install -e .` 로
   git+https 의존성 자동 fetch.
2. `python tools/migrate-to-shared-data-dir.py` 로 dry-run 후 `--confirm`.
3. statement import / 첨부 / 메모는 [whooing-tui](https://github.com/neoocean/whooing-tui)
   의 TUI 화면에서 수행.

### Verified

* pytest -q                    → 188 passed (down from 271 — 83 tests 이전됨).
* make tools                   → 14 tools registered.
* migration script (dry-run)   → 옛 db + attachments 감지 + 새 path 출력.

## v0.1.12 — 2026-05-10

P4 빈 changelist leak 수정 + 테스트 격리 강화.

### Fixed

* `sync_paths_to_p4` 가 `p4 add/edit/submit` 단계 실패 시 numbered CL 을
  서버에 빈 채로 leak 시키던 버그. 이제 실패 시 자동으로 open 된 파일을
  revert + CL 을 `p4 change -d` 로 삭제. (검증 2026-05-10: 60+ 빈 CL 누적
  확인 후 정리.)
* `tests/conftest.py` 신규 — `autouse` fixture 로 p4_sync 강제 disable.
  머신의 실 `whooing-mcp.toml` 이 `enabled=true` 여도 (실 사용자 default)
  pytest 도중 sync 가 fire 하지 않음. tmp_path 가 client view 밖이라
  발생하던 leak 의 근본 원인.

### Added

* `tests/test_p4_sync.py` — 2개 신규 케이스
  (`test_p4_add_failure_deletes_empty_cl`, `test_p4_submit_failure_deletes_empty_cl`)
  로 cleanup mock-검증.
* `_cleanup_failed_cl(cl_num, opened_paths)` private helper — best-effort
  revert + delete.

### Why

사용자 발견: "퍼포스 서브밋할 때 빈 체인지리스트를 남기고 있다." 60개
이상의 leaked CL 을 수동 삭제 + 본 fix 로 미래 leak 방지.

### Verified

* pytest -q → 320 passed (310 기존 + 2 leak fix + 8 conftest 영향 없는 기타).
* Live: 60+ leaked CLs 일괄 삭제 (CL 50764-50917 + 50926).
* P4 `changes -s pending -u woojinkim` → 0 pending (clean).

## v0.1.11 — 2026-05-10

현대카드 HTML 보안메일 (Yettiesoft vestmail) import 지원 + 카드 패스워드
환경변수 통합 (한국 카드사 모두 생년월일 6자리 공통).

### Added

* `src/whooing_mcp/html_adapters/hyundaicard_secure_mail.py` 신규 — Yettiesoft
  vestmail (`eval(atob(b_p))`, `doAction()`) 복호화 + 테이블 행 추출.
* HTML adapter registry 확장: `hanacard_secure_mail` + `hyundaicard_secure_mail`
  자동 detect (1MB head scan — vestmail 마커는 파일 후반부에 위치).
* `tests/test_html_import.py::test_legacy_password_env_var_still_works` —
  옛 키 fallback 검증.
* `Makefile` + `.github/workflows/ci.yml` — Unreleased 에 있던 항목 v0.1.10
  마지막 CL 로 흡수, 본 릴리스에서 작동 확인.

### Changed

* `WHOOING_HANACARD_PASSWORD` → `WHOOING_CARD_HTML_PASSWORD` (rename).
  한국 카드사 모두 사용자 생년월일 6자리 (YYMMDD) 를 보안메일 패스워드로
  사용 → 카드사별 분리 키 불필요. 옛 키는 자동 fallback 으로 backward-compat
  유지 (별도 마이그레이션 불필요).
* `whooing_import_html_statement` 의 `password_env_var` default `auto`
  (기존 하드코딩 제거).
* `html_adapters.detect()` 의 head scan 크기 8KB → 1MB.
* `.env.example` — `WHOOING_CARD_HTML_PASSWORD` 항목 + 설명 추가.

### Why

사용자 요청: "현대카드 HTML 명세서 import 하고 싶다, 그리고 한국 카드사는
모두 생년월일을 패스워드로 쓰니 키를 통합하라."

### Verified

* pytest -q → 310 passed (294 기존 + 16 신규 hyundai unit tests).
* `make tools` → 24 tools.
* live decrypt: `/Users/neoocean/Downloads/hyundaicard_20260425.html` (442KB)
  → 9건 추출 (총 102,309원 = 31,573 일시불 + 70,436 해외 + 300 수수료).
* dry_run → 1건 dedup (카드이용알림수수료04월, entry=1707249) +
  8건 신규 제안.
* 실 입력 → entry_ids 1710783-1710790 (공식 MCP `entries-create`).
  카테고리: 파리바게뜨 6건 → 식비(x50), KCP-알라딘 → 공부(x120),
  Hetzner → 소프트웨어(x99). r_account 모두 [우진]현대카드(x153).
* statement_import_log 동기화: log_id 84-92 → P4 CL 50925 (db auto-sync).

## v0.1.10 — 2026-05-10

P4 sync 다중 파일 일반화 — db + 첨부파일 한 CL 로 자동 submit.

### Added

* `src/whooing_mcp/p4_sync.py` 신규 함수:
  `sync_paths_to_p4(action_summary, paths: list[Path])` — 임의 경로 list 의
  변경 (add/edit) 을 한 numbered CL 로 묶어 submit. 각 path 의 P4 상태 자동
  감지 (depot 미등록 → add + 자동 binary type, 등록됐고 변경 → edit).
* `_p4_filetype()` — 확장자 기반 자동 type 결정 (`.sqlite`, `.pdf`, `.png`,
  `.zip` 등 → 'binary').
* P4IGNORE 우회 (`P4IGNORE=/dev/null`) — `attachments/*` 가 워크스페이스
  ignore 에 안 잡혀도 안전.

### Changed

* `sync_db_to_p4(action_summary)` 는 내부적으로 `sync_paths_to_p4(...,
  paths=[default_queue_path()])` 호출 (backward compat).
* `tools/attachments.py` `attach_file_to_entry` → `sync_paths_to_p4(...,
  paths=[db, copied_file_absolute])`. 응답의 `p4_sync.cl` 한 번에 db + 새
  파일 submit 결과 포함.

### Why

CL 50749 (Atlassian invoice 첨부) 가 manual `p4 add` + `p4 submit` 필요했음
(자동 sync 가 db 만 처리). 사용자 정책 ("매 변경마다 자동 submit") 일관성
복원.

### Verified

* pytest -q → 301 passed.
* Live: 새 첨부파일 + db 모두 단일 CL 로 자동 submit.

## v0.1.9 — 2026-05-09

거래 ↔ 첨부파일 1:N layer.

### Added (도구 3종 — 21 → 24)

* `whooing_attach_file_to_entry(entry_id, file_path, note?, attach_date?, copy=True)`
  파일을 거래에 첨부. 기본 동작: `attachments/files/YYYY/YYYY-MM-DD/` 로 복사
  + sha256 dedup (같은 내용 한 번만 디스크에). copy=False 면 원본 path 그대로 db 만 기록.
* `whooing_list_entry_attachments(entry_ids)` — 한 개 또는 여러 entry 의 첨부파일 조회.
* `whooing_remove_attachment(attachment_id, delete_file=True)` — row + (옵션) 디스크 파일 제거.
  같은 sha256 의 다른 row 가 남아있으면 파일은 보존.

### Added (인프라)

* `src/whooing_mcp/attachments.py` — storage layer (copy + sha256 dedup +
  CRUD + `attach_attachments(entries)` augmentation helper).
* SQLite schema v3 → v4: `entry_attachments` 테이블.
* `audit` + `find_entries_by_hashtag` 출력에 `local_attachments` 필드 자동 부착
  (`local_annotations` 와 같은 패턴).
* `.gitignore`: `attachments/` 차단 (개인 금융 supporting docs — GitHub 절대 X,
  P4 정책상 sync).

### Why
후잉이 거래 항목에 PDF 등 파일 업로드 미지원. 카드 인보이스 같은 supporting
doc 을 거래에 연결하려는 사용자 요청 (2026-05-09). 파일은 로컬 디스크 +
SQLite 메타로 보관, 후잉 ledger 자체는 변경 X.

### First use
* Atlassian Invoice IN-006-462-370 (USD 14.52, 2026-04-29) → entry_id 1710716
  (ATLASSIAN, 22383원, 2026-03-29) 에 첨부 (별도 단계).

## v0.1.8 — 2026-05-09

명세서 자동 import 흐름 완성 + 공식 MCP chained call 도입.

### Added (도구 3종 — 18 → 21)

* **#19 `whooing_delete_entries`** (CL 50717 / git f3b0613) — 공식 후잉 MCP 의
  `entries-delete` 를 chained-call 해 거래 영구 삭제. 본 wrapper 가 직접 후잉
  REST 를 두드리지 않음 (정책 일관성). `confirm=True` 안전 가드 + `statement_import_log`
  자동 동기화. rate limit aware (분당 18 self-throttle).

* **#20 `whooing_import_pdf_statement`** (CL 50719 / git 3440e01) — PDF 카드명세서
  자동 import.
  - `pdf_adapters/` 파싱 (shinhan, hyundai)
  - dedup (paginated `list_entries` 활용)
  - auto-categorize (`suggest_category` 호출)
  - 공식 MCP `entries-create` 로 안전 insert
  - `statement_import_log` tracking
  - `dry_run=True` safety default + `confirm_insert=True` 가드
  - CL 50708 의 일회용 스크립트 (`tests/_pdf_import_2026_04.py`) 일반화.

* **#21 `whooing_import_html_statement`** (CL 50739 / git 01eb2eb) — HTML 보안메일
  자동 import (예: 하나카드 CryptoJS AES 암호화 .html).
  - Playwright 헤드리스 Chromium 으로 client-side 복호화
  - `password_env_var` (default `WHOOING_HANACARD_PASSWORD`) 에서 패스워드 로드
  - DOM 파싱 → PDF import 와 동일한 dedup/categorize/insert 파이프라인 재사용

### Added (인프라)

* `src/whooing_mcp/official_mcp.py` — 공식 MCP HTTP JSON-RPC 클라이언트
  (`OfficialMcpClient.list_tools()` / `call_tool()`). 서버는 stateless —
  init/initialized handshake 없이 단일 request 로 tool 호출. delete + import
  도구의 backbone.
* `src/whooing_mcp/html_adapters/` — pdf_adapters 평행 구조. `base.py`
  (HTMLDetectResult + `decrypt_html_with_playwright()` lazy-import) +
  `hanacard_secure_mail.py`.
* `src/whooing_mcp/client.py` — `list_accounts()` + `flatten_accounts()` —
  PDF/HTML import 의 account_id ↔ (type, name) 매핑용.
* `pyproject.toml` runtime deps 추가: `beautifulsoup4>=4.12`, `playwright>=1.45`
  (Chromium 다운로드 1회 — `playwright install chromium`).
* `tools/pdf_import._log_one()` `source_kind` 파라미터 (기본 'pdf' / HTML 은 'html').

### Fixed (Critical)

* **`client.list_entries`** — 후잉 응답 키 `entries` → 실은 `rows` (CL 50703 /
  git c297054). 모든 read 도구가 실 ledger 에서 빈 결과 반환 중이었음
  (단위 테스트는 FakeClient 분리 때문에 통과해서 안 보임).

* **`client.list_entries`** — 서버 100 hard cap 발견 (CL 50708 / git 9489560).
  `limit` / `page` / `offset` / `X-HTTP-Method-Override` 모두 무동작. **Date-range
  bisection** 으로 자동 분할 + entry_id 기준 dedup. 이전 100건 → 본 fix 후
  122건 (한 달치 활발한 ledger 모두 반환).

### Schema bump v2 → v3

* `statement_import_log` (CL 50708) — PDF/CSV/HTML import audit trail.
  source_file, source_kind ('pdf'|'csv'|'html'), 거래 정보, 후잉 entry_id, status
  ('inserted'|'failed'|'dry_run'|'deleted'|'duplicate_pending_delete'|'inserted_then_duped'),
  imported_at 등 22 필드.

### Known limitations / TODO

* 후잉 REST API 의 DELETE entry 형식이 우리 직접 호출에 응답 안 함 — 5+ 패턴
  시도 모두 실패 ("section_id parameter is required" 또는 "Unknown method").
  → **`whooing_delete_entries` 가 공식 MCP `entries-delete` 를 통과** 함으로써
  우회 (검증된 호출 형식). 본 wrapper read-only 정책 일부 예외.
* `hanacard_secure_mail` 파서는 정형 거래 섹션 (5-cell 패턴) 에 최적화.
  **해외이용내역 상세** 섹션 (date / 국가 / 도시 / merchant / currency /
  amount / rate / KRW / fee 컬럼) 에서는 카드번호 같은 값이 merchant 로
  잡히는 케이스. 후속 CL 에서 섹션 검출 + layout 분기.
* PDF import 는 텍스트 추출 가능 PDF 만 — 이미지 PDF 는 OCR 필요 (deferred).

### Developer scripts (committed for posterity)

* `tests/_pdf_import_2026_04.py` — 2026-04 하나카드 명세서 import 일회용
  reference (정식 `whooing_import_pdf_statement` 의 prototype).
* `tests/_probe_s9046.py` — accounts/entries 구조 탐색 helper.
* `tests/_decrypt_hanacard_html.py` — 하나카드 HTML 복호화 일회용 검증
  reference (정식 `whooing_import_html_statement` 의 prototype).

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
