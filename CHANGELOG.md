# Changelog

[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 형식.
모든 P4 changelist 와 1:1 대응. 외부 노출명은 v0.1 부터 `whooing-mcp-server-wrapper`.

## Unreleased

* 다음 마일스톤: P1 SMS issuer 5종 추가, P1 CSV adapter 추가, **PDF 임포트 지원** (신규).
* 그 다음: P2 `whooing_monthly_close` (audit + dedup + reconcile + suggest_category 합성).

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
