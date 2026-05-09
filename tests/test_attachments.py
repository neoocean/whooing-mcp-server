"""attachments.py + tools/attachments.py 회귀."""

from __future__ import annotations

from pathlib import Path

import pytest

from whooing_mcp.attachments import (
    attach_attachments,
    copy_to_attachments,
    default_attachments_root,
    delete_attachment,
    list_attachments_for,
    upsert_attachment,
)
from whooing_mcp.models import ToolError
from whooing_mcp.queue import open_db
from whooing_mcp.tools import attachments as tools_attach


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    """db + attachments root 모두 tmp 로 격리."""
    db = tmp_path / "queue.db"
    monkeypatch.setenv("WHOOING_QUEUE_PATH", str(db))
    att_root = tmp_path / "att" / "files"
    monkeypatch.setenv("WHOOING_ATTACHMENTS_ROOT", str(att_root))
    yield {"tmp": tmp_path, "att_root": att_root}


def _make_dummy_pdf(path: Path, content: bytes = b"%PDF-1.4\n...dummy...\n%%EOF\n") -> Path:
    path.write_bytes(content)
    return path


# ---- copy_to_attachments ---------------------------------------------


def test_copy_creates_dated_dir(tmp_env):
    src = _make_dummy_pdf(tmp_env["tmp"] / "src.pdf")
    copied, sha, size = copy_to_attachments(src, attach_date="2026-05-09")
    assert copied.parent == tmp_env["att_root"] / "2026" / "2026-05-09"
    assert copied.name == "src.pdf"
    assert copied.exists()
    assert size == src.stat().st_size
    assert len(sha) == 64


def test_copy_dedups_same_sha(tmp_env):
    src = _make_dummy_pdf(tmp_env["tmp"] / "x.pdf", content=b"identical-content")
    p1, sha1, _ = copy_to_attachments(src, attach_date="2026-05-09")
    # 다시 같은 source — 이미 같은 위치에 같은 sha 있으면 reuse
    p2, sha2, _ = copy_to_attachments(src, attach_date="2026-05-09")
    assert p1 == p2
    assert sha1 == sha2


def test_copy_renames_on_collision(tmp_env):
    """같은 이름이지만 다른 내용이면 -1, -2 suffix."""
    a = _make_dummy_pdf(tmp_env["tmp"] / "doc.pdf", content=b"content-A")
    b = _make_dummy_pdf(tmp_env["tmp"] / "doc.pdf.tmp", content=b"content-B")
    # rename .tmp → doc.pdf at copy time
    pa, sa, _ = copy_to_attachments(a, attach_date="2026-05-09")
    # b 를 doc.pdf 이름으로 위장 (test sake)
    b_renamed = tmp_env["tmp"] / "doc2.pdf"
    b_renamed.write_bytes(b.read_bytes())
    # 실 src 가 다른 이름이라 copy 는 정상 생성 — collision 시나리오 직접 작성:
    # 같은 이름의 다른 내용 src 파일을 만들어서 (같은 디렉터리):
    src2 = tmp_env["tmp"] / "subdir2"
    src2.mkdir()
    diff = _make_dummy_pdf(src2 / "doc.pdf", content=b"different-content")
    pb, sb, _ = copy_to_attachments(diff, attach_date="2026-05-09")
    # collision → suffix
    assert pb.name == "doc-1.pdf"
    assert sa != sb


def test_copy_rejects_missing_file(tmp_env):
    with pytest.raises(FileNotFoundError):
        copy_to_attachments(Path("/tmp/__nonexistent_xxxxx.pdf"))


def test_copy_rejects_directory(tmp_env):
    with pytest.raises(ValueError):
        copy_to_attachments(tmp_env["tmp"])


# ---- db CRUD ---------------------------------------------------------


def test_upsert_inserts_new(tmp_env):
    with open_db() as conn:
        row = upsert_attachment(
            conn, entry_id="1234", section_id="s_FAKE",
            file_path="att/files/2026/2026-05-09/foo.pdf",
            original_path="/tmp/orig/foo.pdf",
            original_filename="foo.pdf",
            file_size_bytes=1024, file_sha256="abc"*16, mime_type="application/pdf",
            note="invoice",
        )
    assert row["entry_id"] == "1234"
    assert row["original_filename"] == "foo.pdf"


def test_upsert_dedupes_same_sha_for_same_entry(tmp_env):
    with open_db() as conn:
        row1 = upsert_attachment(
            conn, entry_id="e1", section_id=None,
            file_path="p1", original_path=None, original_filename="x.pdf",
            file_size_bytes=10, file_sha256="hash-A", mime_type="application/pdf", note=None,
        )
        row2 = upsert_attachment(
            conn, entry_id="e1", section_id=None,
            file_path="p2", original_path=None, original_filename="x.pdf",
            file_size_bytes=10, file_sha256="hash-A", mime_type="application/pdf", note=None,
        )
    assert row1["id"] == row2["id"]


def test_list_attachments_for_multi_entries(tmp_env):
    with open_db() as conn:
        upsert_attachment(conn, entry_id="e1", section_id=None, file_path="p1",
                          original_path=None, original_filename="a.pdf",
                          file_size_bytes=1, file_sha256="sha1",
                          mime_type=None, note=None)
        upsert_attachment(conn, entry_id="e1", section_id=None, file_path="p2",
                          original_path=None, original_filename="b.pdf",
                          file_size_bytes=1, file_sha256="sha2",
                          mime_type=None, note=None)
        upsert_attachment(conn, entry_id="e2", section_id=None, file_path="p3",
                          original_path=None, original_filename="c.pdf",
                          file_size_bytes=1, file_sha256="sha3",
                          mime_type=None, note=None)
    with open_db() as conn:
        m = list_attachments_for(conn, ["e1", "e2", "e3"])
    assert len(m["e1"]) == 2
    assert len(m["e2"]) == 1
    assert "e3" not in m


def test_delete_attachment_removes_row(tmp_env):
    with open_db() as conn:
        row = upsert_attachment(
            conn, entry_id="e1", section_id=None, file_path="path",
            original_path=None, original_filename="x.pdf",
            file_size_bytes=1, file_sha256="sha", mime_type=None, note=None,
        )
    with open_db() as conn:
        deleted = delete_attachment(conn, row["id"], delete_file=False)
    assert deleted is not None
    with open_db() as conn:
        m = list_attachments_for(conn, ["e1"])
    assert "e1" not in m


# ---- attach_attachments helper --------------------------------------


def test_attach_attachments_no_data(tmp_env):
    entries = [{"entry_id": "1234", "money": 100}]
    out = attach_attachments(entries)
    assert out[0]["local_attachments"] == []


def test_attach_attachments_with_data(tmp_env):
    with open_db() as conn:
        upsert_attachment(
            conn, entry_id="1234", section_id=None, file_path="p",
            original_path=None, original_filename="x.pdf",
            file_size_bytes=10, file_sha256="sha", mime_type="application/pdf",
            note="invoice",
        )
    out = attach_attachments([{"entry_id": "1234", "money": 100}])
    assert len(out[0]["local_attachments"]) == 1
    assert out[0]["local_attachments"][0]["original_filename"] == "x.pdf"


def test_attach_attachments_does_not_mutate_input(tmp_env):
    entries = [{"entry_id": "9", "money": 1}]
    attach_attachments(entries)
    assert "local_attachments" not in entries[0]


# ---- tools/attachments envelope -----------------------------------


async def test_attach_file_to_entry_basic(tmp_env):
    src = _make_dummy_pdf(tmp_env["tmp"] / "test.pdf")
    out = await tools_attach.attach_file_to_entry(
        entry_id="9999", file_path=str(src), note="test"
    )
    assert out["copied"] is True
    assert out["attachment"]["entry_id"] == "9999"
    assert out["attachment"]["original_filename"] == "test.pdf"
    assert out["attachment"]["mime_type"] == "application/pdf"


async def test_attach_file_relative_path_rejected(tmp_env):
    with pytest.raises(ToolError) as ex:
        await tools_attach.attach_file_to_entry(
            entry_id="9", file_path="relative.pdf"
        )
    assert ex.value.kind == "USER_INPUT"


async def test_attach_file_missing_rejected(tmp_env):
    with pytest.raises(ToolError):
        await tools_attach.attach_file_to_entry(
            entry_id="9", file_path="/tmp/__nonexistent_yzx.pdf"
        )


async def test_attach_file_invalid_date_rejected(tmp_env):
    src = _make_dummy_pdf(tmp_env["tmp"] / "x.pdf")
    with pytest.raises(ToolError):
        await tools_attach.attach_file_to_entry(
            entry_id="9", file_path=str(src), attach_date="not-a-date"
        )


async def test_list_entry_attachments(tmp_env):
    src = _make_dummy_pdf(tmp_env["tmp"] / "x.pdf")
    await tools_attach.attach_file_to_entry(entry_id="1", file_path=str(src))
    src2 = _make_dummy_pdf(tmp_env["tmp"] / "y.pdf", content=b"different")
    await tools_attach.attach_file_to_entry(entry_id="2", file_path=str(src2))

    out = await tools_attach.list_entry_attachments(["1", "2", "3"])
    assert out["found_entries"] == 2
    assert out["total_attachments"] == 2
    assert "1" in out["attachments_by_entry"]
    assert "3" not in out["attachments_by_entry"]


async def test_remove_attachment(tmp_env):
    src = _make_dummy_pdf(tmp_env["tmp"] / "x.pdf")
    out = await tools_attach.attach_file_to_entry(entry_id="1", file_path=str(src))
    aid = out["attachment"]["id"]
    deleted = await tools_attach.remove_attachment(attachment_id=aid, delete_file=True)
    assert deleted["removed"] is True
    assert deleted["file_deleted"] is True


async def test_remove_attachment_unknown_id(tmp_env):
    with pytest.raises(ToolError):
        await tools_attach.remove_attachment(attachment_id=99999)
