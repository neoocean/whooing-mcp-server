# whooing-mcp

후잉 가계부([whooing.com](https://whooing.com))의 **공식 MCP 서버**(`https://whooing.com/mcp`)
위에서 동작하는 **보완 도구 묶음**입니다. Claude Code / Claude Desktop 사용자는
공식 MCP 와 본 wrapper MCP 를 함께 등록해, 자연어로 가계부를 다루면서 공식이
제공하지 않는 영역(SMS 결제알림 파싱 / LLM 입력 audit / 카드명세서 CSV 정산)을
추가로 사용합니다.

> 본 서버는 **거래 입력/수정/삭제 같은 기본 CRUD 는 제공하지 않습니다.** 그것은
> 공식 MCP (`https://whooing.com/mcp`) 가 합니다. 본 서버를 단독으로 등록해선
> 의미가 적습니다 — 공식과 함께 등록하세요.

## v0.1 (CL #1) 도구 1개

| 도구 | 설명 |
|---|---|
| `whooing_audit_recent_ai_entries` | 최근 N일 거래 중 LLM 이 입력한 것만 골라봅니다 (memo 접두 마커 기준) |

다음 CL 에서 추가 예정: `whooing_find_duplicates`, `whooing_parse_payment_sms`,
`whooing_reconcile_csv`, `whooing_csv_format_detect` (DESIGN.md §6 참조).

## Quickstart

### 1. AI 연동 토큰 발급

후잉 → **사용자 > 계정 > 비밀번호 및 보안 > AI 토큰 발급**

발급된 토큰은 `__eyJh...` 로 시작합니다. **앞 underscore 2개 포함 전체**가
토큰입니다 (자주 놓치는 함정).

scope 는 본 서버 v0.1 기준 `read` 만 있으면 됩니다 (입력은 공식 MCP 가 함).

### 2. 설치

```bash
git clone https://github.com/neoocean/whooing-mcp-server
cd whooing-mcp-server
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 3. `.env` 작성

```bash
cp .env.example .env
$EDITOR .env  # WHOOING_AI_TOKEN 채우기
```

`WHOOING_SECTION_ID` 는 옵션입니다 (미설정 시 첫 섹션 자동). 여러 가계부를
가지셨다면 명시 권장.

### 4. Claude Desktop 등록 — 공식 + 우리 wrapper 둘 다

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "whooing": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://whooing.com/mcp",
               "--header", "X-API-Key: __eyJh..."]
    },
    "whooing-extras": {
      "command": "python",
      "args": ["-m", "whooing_mcp"],
      "env": {
        "WHOOING_AI_TOKEN": "__eyJh..."
      }
    }
  }
}
```

`examples/claude_desktop_config.json` 에 동일한 템플릿이 있습니다.

### 5. Claude Code (CLI)

```bash
# 공식 MCP
claude mcp add --transport http whooing https://whooing.com/mcp \
  --header "X-API-Key: __eyJh..." --scope user

# 본 wrapper (cwd 가 이 프로젝트일 때 .env 자동 로드)
claude mcp add whooing-extras python -m whooing_mcp --scope user
```

## `[ai]` 마커 컨벤션 — 중요

`whooing_audit_recent_ai_entries` 가 LLM 입력 거래를 추적하려면 **컨벤션**이
필요합니다. 공식 MCP 의 `add_entry` 도구에 우리가 hook 을 못 거므로, LLM 에게
"사용자 위임으로 거래를 입력할 때 memo 첫 단어로 `[ai]` 를 붙여라" 라고
안내해야 합니다.

Claude 와의 대화 시작 시 다음과 같이 시스템 프롬프트나 첫 발화에 박아두면
좋습니다:

> 후잉 가계부에 거래를 입력할 때, 내가 명시적으로 위임한 경우 memo 의 첫
> 단어를 `[ai]` 로 시작해줘. 예: `memo="[ai] 강남 스타벅스"`. 그래야
> `whooing_audit_recent_ai_entries` 로 나중에 추적할 수 있어.

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `WHOOING_AI_TOKEN 미설정` | `.env` 파일이 cwd 에 없거나 키 이름 오타. `.env.example` 참고. |
| 도구 응답에 `error.kind="AUTH"` | 토큰 만료/revoke. 후잉에서 재발급 후 .env 갱신. |
| 도구 응답에 `error.kind="RATE_LIMIT"` | 분당 20회 / 일 20,000회 한도. `rest_of_api` 잔량 확인. |
| 의도와 다른 가계부 데이터가 보임 | `WHOOING_SECTION_ID` 미설정 → 첫 섹션 자동 선택됨. .env 에 명시. |

## 라이선스

MIT.

## 설계 / 결정 이력

- [DESIGN.md](DESIGN.md) — 전체 설계 (v2 wrapper 모델).
- v1 (자체 구현 안) 폐기 사유는 DESIGN §0, §3 참조.
