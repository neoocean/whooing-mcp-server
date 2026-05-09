#!/usr/bin/env bash
# whooing-mcp-remote.sh
# .env 에서 WHOOING_AI_TOKEN 을 읽어 mcp-remote 의 --header X-API-Key 로
# 넘기는 wrapper. 후잉 공식 MCP (https://whooing.com/mcp) 등록 시 사용.
#
# .env 탐색 우선순위 (먼저 발견된 1개):
#   1. $WHOOING_MCP_ENV
#   2. <이 스크립트의 프로젝트 루트>/.env
#   3. ~/.config/whooing-mcp/.env
#
# Claude Desktop config 사용 예:
#   {
#     "mcpServers": {
#       "whooing": {
#         "command": "/abs/path/to/whooing-mcp-server-wrapper/bin/whooing-mcp-remote.sh"
#       }
#     }
#   }
#
# 등록 후 토큰 갱신은 .env 수정만으로 끝 — config 재배포 불필요.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

candidates=(
    "${WHOOING_MCP_ENV:-}"
    "${script_dir}/../.env"
    "${HOME}/.config/whooing-mcp/.env"
)

env_file=""
for c in "${candidates[@]}"; do
    if [[ -n "$c" && -f "$c" ]]; then
        env_file="$c"
        break
    fi
done

if [[ -z "$env_file" ]]; then
    {
        echo "whooing-mcp-remote: no .env found. Tried:"
        for c in "${candidates[@]}"; do
            [[ -n "$c" ]] && echo "  - $c"
        done
        echo "Set WHOOING_MCP_ENV or create one of the above."
    } >&2
    exit 1
fi

# .env 를 안전하게 source — set -a 로 모든 KEY=VALUE 를 export 처리.
# JWT 토큰 형태 (alphanumeric + . _ -) 는 따옴표 없이 valid bash assignment.
set -a
# shellcheck disable=SC1090
. "$env_file"
set +a

if [[ -z "${WHOOING_AI_TOKEN:-}" ]]; then
    echo "whooing-mcp-remote: WHOOING_AI_TOKEN not set in $env_file" >&2
    exit 1
fi

exec npx -y mcp-remote https://whooing.com/mcp \
    --header "X-API-Key: ${WHOOING_AI_TOKEN}"
