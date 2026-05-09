"""official_mcp.py — JSON-RPC 호출 회귀 (httpx mock)."""

from __future__ import annotations

import json

import httpx
import pytest

from whooing_mcp.official_mcp import OfficialMcpClient, OfficialMcpError


def _mock_response(json_body: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        content=json.dumps(json_body).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


@pytest.fixture
def client():
    return OfficialMcpClient(token="__eyJh" + "x" * 100)


# ---- list_tools --------------------------------------------------------


async def test_list_tools_parses(client, monkeypatch):
    captured: dict = {}

    async def fake_post(self, url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        return _mock_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {"tools": [
                {"name": "entries-delete", "description": "삭제"},
                {"name": "entries-create", "description": "생성"},
            ]},
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    tools = await client.list_tools()
    assert len(tools) == 2
    assert tools[0]["name"] == "entries-delete"
    # verify request shape
    assert captured["json"]["method"] == "tools/list"
    assert captured["headers"]["X-API-Key"].startswith("__eyJh")


async def test_list_tools_empty_result(client, monkeypatch):
    async def fake_post(self, url, **kwargs):
        return _mock_response({"jsonrpc": "2.0", "id": 1, "result": {}})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    assert await client.list_tools() == []


# ---- call_tool ---------------------------------------------------------


async def test_call_tool_success(client, monkeypatch):
    captured: dict = {}

    async def fake_post(self, url, **kwargs):
        captured["json"] = kwargs.get("json")
        return _mock_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {
                "content": [{"type": "text", "text": "삭제 완료"}],
                "isError": False,
            },
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = await client.call_tool("entries-delete",
                                     {"section_id": "s9046", "entry_id": "1234"})
    assert result["content"][0]["text"] == "삭제 완료"
    assert captured["json"]["method"] == "tools/call"
    assert captured["json"]["params"]["name"] == "entries-delete"
    assert captured["json"]["params"]["arguments"]["entry_id"] == "1234"


async def test_call_tool_isError_raises(client, monkeypatch):
    async def fake_post(self, url, **kwargs):
        return _mock_response({
            "jsonrpc": "2.0", "id": 1,
            "result": {
                "content": [{"type": "text", "text": "거래를 찾을 수 없습니다"}],
                "isError": True,
            },
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(OfficialMcpError) as ex:
        await client.call_tool("entries-delete", {"section_id": "x", "entry_id": "y"})
    assert "찾을 수 없습니다" in str(ex.value)


async def test_call_tool_jsonrpc_error_raises(client, monkeypatch):
    async def fake_post(self, url, **kwargs):
        return _mock_response({
            "jsonrpc": "2.0", "id": 1,
            "error": {"code": -32601, "message": "Method not found"},
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(OfficialMcpError) as ex:
        await client.call_tool("nonexistent", {})
    assert ex.value.code == -32601
    assert "Method not found" in str(ex.value)


async def test_call_tool_non_json_response(client, monkeypatch):
    async def fake_post(self, url, **kwargs):
        return httpx.Response(status_code=502, content=b"<html>bad gateway</html>")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(OfficialMcpError) as ex:
        await client.call_tool("anything", {})
    assert "non-JSON" in str(ex.value)


async def test_call_tool_missing_result(client, monkeypatch):
    async def fake_post(self, url, **kwargs):
        return _mock_response({"jsonrpc": "2.0", "id": 1})  # no error, no result

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(OfficialMcpError) as ex:
        await client.call_tool("anything", {})
    assert "missing 'result'" in str(ex.value)


# ---- request id increments --------------------------------------------


async def test_request_id_increments(client, monkeypatch):
    ids: list[int] = []

    async def fake_post(self, url, **kwargs):
        ids.append(kwargs["json"]["id"])
        return _mock_response({"jsonrpc": "2.0", "id": kwargs["json"]["id"], "result": {}})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    await client.list_tools()
    await client.list_tools()
    await client.call_tool("foo", {})
    assert ids == [1, 2, 3]
