# `attachments/` 디렉터리

후잉 거래 항목에 연결된 supporting 문서 (PDF 인보이스 / 영수증 사진 / 계약서 등)
의 로컬 저장소. 후잉이 entry-attachment 를 미지원해서 본 wrapper 가 별도 layer
로 운영한다 (DESIGN §6.X / CHANGELOG v0.1.9 참조).

## 디렉터리 구조

```
attachments/
├── README.md                           ← 본 파일 (git 에 commit)
└── files/
    └── YYYY/
        └── YYYY-MM-DD/
            └── <original-filename>     ← 실제 파일 (git 에서 차단)
```

`YYYY-MM-DD` 는 첨부 시각 기준 (= `whooing_attach_file_to_entry` 호출 날짜).
파일 추적은 디렉터리가 아니라 SQLite (`entry_attachments` 테이블 + `entry_id`
매핑) 가 담당하므로, 디렉터리 분류는 단순 운영 편의용.

## 어떻게 들어가나

`whooing_attach_file_to_entry(entry_id, file_path, ...)` 도구 호출 시:

1. SHA256 으로 dedup — 같은 내용이 이미 있으면 디스크 재복사 안 함 (db row 만 추가).
2. `attachments/files/YYYY/YYYY-MM-DD/<basename>` 으로 `shutil.copy2`.
3. 같은 이름·다른 내용이면 `<stem>-1.<ext>` 식 suffix.
4. SQLite 의 `entry_attachments` 테이블에 link record (`entry_id`, `file_path`,
   `file_sha256`, `mime_type`, `note`, `attached_at`) 저장.

## 어떻게 조회하나

* `whooing_list_entry_attachments(entry_ids)` — 한 개 / 여러 entry 의 첨부 list.
* `whooing_audit_recent_ai_entries` / `whooing_find_entries_by_hashtag` 응답에
  각 entry 의 `local_attachments` 필드가 자동 부착됨.

`file_path` 는 프로젝트 루트 기준 relative — 다른 머신에서 P4 sync 후에도
같은 경로로 접근 가능.

## 어떻게 지우나

`whooing_remove_attachment(attachment_id, delete_file=True)`:

* row 제거.
* `delete_file=True` 면 디스크 파일도 제거. **단** 같은 sha256 의 다른 row 가
  남아있으면 (다른 entry 가 같은 파일 참조) 파일은 보존.

## 동기화 정책

| | 동작 |
|---|---|
| **GitHub** | **`attachments/README.md` (본 파일) 만 commit. 나머지 모두 차단** (개인 금융 supporting docs — 영수증/카드명세서/인보이스 등 외부 노출 금지). |
| **Perforce** | **`attachments/` 전체 sync** (cross-machine 정책). p4d 가 비공개 개인 서버라 안전. db (`whooing-data.sqlite`) 는 modifying 도구 호출 직후 자동 P4 sync — 첨부파일도 같이 보내려면 별도 `p4 reconcile + submit` 필요 (자동 sync 는 db 만). |

`.gitignore` 의 관련 룰:

```
attachments/*
!attachments/README.md
```

## 새 머신에서 복원

```bash
p4 sync //woojinkim/scripts/whooing-mcp-server/attachments/...
```

이후 도구 호출이 `attachments/files/` 의 파일들을 그대로 읽음 (db 의
`file_path` 가 relative 라 머신 무관).

## 백업 / 정리 권장

* 매월 끝에 `attachments/files/<YYYY>/` 의 크기 점검. 너무 크면 별도 archive
  (예: zip + Backblaze 등) 옮기고 db 의 `file_path` 갱신 (수동).
* 누락된 (지운) 파일은 `whooing_list_entry_attachments` 응답으로는 보이지만
  실제로 열기 시 `FileNotFoundError`. `whooing_remove_attachment` 로 정리.

## 관련 문서

* [DESIGN.md](../DESIGN.md) — 전체 설계
* [CHANGELOG.md](../CHANGELOG.md) — v0.1.9 entry
* [README.md](../README.md) — wrapper 도구 사용법
