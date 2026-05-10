# 흡수 결정 과정 — 2026-05-10

본 문서는 `whooing-mcp-server-wrapper` 가 `whooing-tui` monorepo 로 흡수된
의사결정 흐름을 보존합니다. README.md 는 결과를 요약하지만, 본 문서는 그
결과에 이른 대화와 trade-off 분석을 verbatim 으로 기록합니다.

## 1. 배경 — v0.2.0 시점의 상황

2026-05-10 오후 시점 두 프로젝트의 관계:

```
whooing-mcp-server-wrapper/                ← 이 repo (별도 GitHub)
└── 의존: whooing-core (sibling at scripts/whooing-tui/core)

whooing-tui/                                ← monorepo (별도 GitHub)
├── core/    (whooing-core 라이브러리 — 어댑터 / db / attachments)
└── tui/     (Textual app)
```

수일에 걸쳐 진행된 작업:
- whooing-core 라이브러리 추출 (Phase 1)
- wrapper 에서 10 도구 제거 (Phase 2)
- SQLite read-only 분리 (Phase 3)
- WHOOING_DATA_DIR 공유 path (Phase 4)
- v0.2.0 release + migration script (Phase 5)
- whooing-tui 의 4 신규 화면 (Phase 6)

이 시점에서 두 프로젝트는 같은 머신에서 같은 .env, 같은 ~/.whooing/ 데이터,
같은 whooing-core 코드를 공유하고 있었습니다.

## 2. 사용자가 던진 질문

> "개인이 사용할 whooing-tui 를 만들고 있습니다. 이 상황에서 whooing mcp wrapper
> 가 독립적으로 존재할 필요성이 충분한지 아니면 whooing-tui 프로젝트로 흡수되는
> 것이 나을지 검토해주세뇨."

## 3. 분석 (양쪽 관점 정직 비교)

### 흡수 (monorepo) 가 나은 이유

| 항목 | 분리 유지 | 흡수 후 |
|---|---|---|
| Git repo | 2개 (push 2번) | 1개 |
| Perforce path | 2 dir | 1 dir |
| CHANGELOG | 2개 (이중 기록) | 1개 |
| 버전 bump | 2번 (어긋남) | 1번 |
| 데이터 모델 변경 시 CL | 2 repo 코디네이트 | 1 repo |
| GitHub Actions | 2개 | 1개 (matrix) |
| 외부 contributor 협업 | 분리가 명확 | (1인 — 무관) |

핵심 통찰: **"분리가 가치 있는 이유"는 보통 audience 가 다를 때**. 다른 사람이
wrapper 만 쓰거나 TUI 만 쓰는 시나리오가 있으면 분리가 의미 있지만, 1인
사용 환경에서는 분리가 만드는 가치 (격리, 독립적 release) 가 거의 0.

### 분리를 유지해야 할 이유 (정직하게 따져 봄)

1. **MCP 프로토콜 update 가 잦으면 wrapper 만 자주 release 하고 싶을 수 있다**
   — 실제로는 mcp SDK 의 변경이 그렇게 잦지 않음. 본 wrapper 의 14 도구가
   안정화된 후로 schema 변경 거의 없음.
2. **나중에 wrapper 만 다른 사람에게 공유할 일이 있을 수도 있다** —
   monorepo subdirectory 링크 (`/blob/main/mcp/...`) 로 가능. 분리 repo 가
   필수는 아님.
3. **Claude Desktop config 경로가 바뀌면 unsetup 필요** — 1회성 작업.
4. **이미 분리 작업에 시간을 썼다** (sunk cost) — 분리 자체가 잘못된 게 아니라
   **데이터 ownership / 어댑터 추출** 작업이 핵심이었고, 그건 monorepo 안에
   서도 그대로 유효. 잃는 것은 "별개 repo" 라는 layer 만.

### 권장 (그리고 사용자 결정)

> 권장: 흡수가 더 낫습니다. 1인 사용 컨텍스트에서 분리가 만들어내는 ceremony
> (push 2번 / version 2개 / CHANGELOG 2개 / repo 관리 2개) 가 정당화되지
> 않습니다.

사용자 응답:

> "whooing-tui로 흡수하고 이 프로젝트는 지금까지의 진행, 흡수 결정 과정,
> 흡수된 리파지토리 안내를 기록하고 마무리하겠습니다. 현재
> whooing-mcp-server-wrapper는 archive 하겠습니다."

## 4. 실행 (2026-05-10)

### 흡수 (whooing-tui CL 50988)

- `whooing-mcp-server/` → `whooing-tui/mcp/` 전체 이전 (`p4 move` 89 파일,
  history 보존)
- `mcp/pyproject.toml` 갱신: name `whooing-mcp-server-wrapper` → `whooing-mcp-server`,
  whooing-core 가 sibling path 로 import
- `mcp/Makefile` 갱신: monorepo 루트의 `.venv` 공유
- 루트 `Makefile` 에 mcp install/test target 추가
- `mcp/tests/test_queue.py` + `test_server_env.py`: 옛 project root 가정 테스트
  새 위치로 갱신

검증: `make test` → core 72 + tui 170 + mcp 189 = **431 passed**.

### 본 repo archive (이 CL)

- 본 P4 dir 의 모든 컨텐츠는 위 CL 에서 이미 새 위치로 이전됨 → 본 repo 의
  P4 working set 에는 코드/테스트가 없음.
- 본 CL 에서 추가: `README.md` (ARCHIVED 안내) + `ARCHIVED.md` (본 문서).
- GitHub repo `neoocean/whooing-mcp-server-wrapper` 는 GitHub archive flag
  (`gh repo archive`) 로 read-only 표시.

## 5. 보존되는 것 / 보존되지 않는 것

### 보존됨

- **P4 history**: `p4 changes //woojinkim/scripts/whooing-mcp-server/...` 로
  CL #50624 (초기) ~ #50988 (이전 직전) 까지 모두 조회 가능.
- **Git history**: GitHub 의 `neoocean/whooing-mcp-server-wrapper` 가 archive
  되어도 commit / tag 모두 read-only 로 접근 가능.
- **이전된 코드**: whooing-tui/mcp/ 안에서 그대로 동작. v0.2.1+ 로 계속 진화.
- **CHANGELOG**: 옛 항목은 `mcp/CHANGELOG.md` 에 v0.1.0 ~ v0.2.0 모두 그대로.

### 보존되지 않는 것 (의도)

- **별도 repo 의 새 commit**: 본 archive 후로는 본 repo 에 새 코드 / CL 추가
  하지 않음.
- **별도 PyPI / GitHub Actions / Issues**: 활동 모두 whooing-tui 로.

## 6. 사용자 측 마이그레이션 (Claude Desktop 설정)

기존:
```bash
claude mcp add whooing-extras /Users/neoocean/Perforce/surface/scripts/whooing-mcp-server/.venv/bin/python -m whooing_mcp
```

새 위치:
```bash
claude mcp remove whooing-extras   # 옛 entry 제거
claude mcp add whooing-extras /Users/neoocean/Perforce/surface/scripts/whooing-tui/.venv/bin/python -m whooing_mcp
```

또는 Claude Desktop config json 에서 `command` path 를 직접 수정.
