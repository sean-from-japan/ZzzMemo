"""Tests for images, Word, Markdown and text files, and documents written in
the app ("document" fields): how they are stored, shown and exported.

Each test gets its own temporary DB and files folder; nothing under data/ is
touched. The documents are made-up samples.
"""

import io

import pytest
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import db
from web.routers import api

AGENT = {"X-Actor": "claude"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def sample_docx() -> bytes:
    d = Document()
    d.add_heading("見本の題名", level=1)
    d.add_paragraph("見本の本文です。")
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "test.db")
    db.init()
    app = FastAPI()
    app.include_router(api.router)
    c = TestClient(app)
    # made by the user, so active at once
    assert c.post("/api/kinds", json={"name": "資料", "fields": [{"name": "ファイル", "type": "file"}]}).status_code == 200
    assert c.post("/api/kinds", json={"name": "文書", "fields": [
        {"name": "本文", "type": "document"}, {"name": "分類", "type": "choice", "options": ["授業", "就活"]},
    ]}).status_code == 200
    return c


def upload(client, data, name):
    return client.post("/api/files", params={"name": name}, content=data)


def file_item(client, data, name, title):
    file_id = upload(client, data, name).json()["id"]
    client.post("/api/items", json={"kind": "資料", "title": title, "fields": {"ファイル": file_id}})
    return file_id


def test_images_are_served_as_they_are(client):
    file_id = file_item(client, PNG, "写真.PNG", "写真")
    assert file_id.endswith(".png")
    r = client.get(f"/files/{file_id}")
    assert r.headers["content-type"] == "image/png" and r.content == PNG
    assert upload(client, b"\xff\xd8\xff\xe0rest", "a.jpeg").json()["id"].endswith(".jpg")


def test_word_files_are_shown_as_a_page_and_downloaded_as_they_are(client):
    data = sample_docx()
    file_id = file_item(client, data, "見本.docx", "見本の文書")
    r = client.get(f"/files/{file_id}")
    assert r.headers["content-type"].startswith("text/html")
    # no scripts at all on a page made from a file
    assert r.headers["content-security-policy"] == "sandbox"
    assert "<h1>見本の題名</h1>" in r.text and "<title>見本の文書</title>" in r.text
    d = client.get(f"/files/{file_id}", params={"download": 1})
    assert d.content == data
    assert "attachment" in d.headers["content-disposition"]
    assert "%E8%A6%8B%E6%9C%AC%E3%81%AE%E6%96%87%E6%9B%B8.docx" in d.headers["content-disposition"]


def test_markdown_and_text_files_are_shown_as_pages(client):
    md = file_item(client, "# 見出し\n\n<script>alert(1)</script>".encode(), "a.md", "メモ")
    r = client.get(f"/files/{md}")
    assert "<h1>見出し</h1>" in r.text and "&lt;script&gt;" in r.text
    # a Windows text file in Shift_JIS
    txt = file_item(client, "こんにちは <b>".encode("cp932"), "a.txt", "テキスト")
    assert "こんにちは &lt;b&gt;" in client.get(f"/files/{txt}").text


def test_files_that_are_not_what_they_say_are_refused(client):
    for data, name in [(b"not an image", "a.png"), (b"PK\x03\x04broken", "a.docx"),
                       (b"bin\x00ary", "a.txt"), (PNG, "a.heic"), (b"<svg/>", "a.svg")]:
        assert upload(client, data, name).status_code == 400, name


def test_a_document_is_written_shown_and_exported(client):
    item = client.post("/api/items", json={"kind": "文書", "title": "レポートの下書き", "fields": {"分類": "授業"}}).json()
    text = "# 序論\n\n本文の**太字**。\n\n- 一つ目\n- 二つ目\n\n| 項目 | 内容 |\n| --- | --- |\n| a | b |\n\n<script>x</script>"
    saved = client.patch(f"/api/items/{item['id']}", json={"fields": {"本文": text}}).json()
    # the other fields stay
    assert saved["fields"] == {"分類": "授業", "本文": text}

    page = client.get(f"/api/items/{item['id']}/document")
    assert page.headers["content-security-policy"] == "sandbox allow-scripts allow-modals"
    assert "<h1>序論</h1>" in page.text and "<strong>太字</strong>" in page.text
    assert "<script>x</script>" not in page.text and "zzzScroll" in page.text
    assert "print()" in client.get(f"/api/items/{item['id']}/document", params={"print": 1}).text

    r = client.get(f"/api/items/{item['id']}/document.docx")
    assert "attachment" in r.headers["content-disposition"]
    d = Document(io.BytesIO(r.content))
    assert [(p.style.name, p.text) for p in d.paragraphs][:4] == [
        ("Heading 1", "序論"), ("Normal", "本文の太字。"), ("List Bullet", "一つ目"), ("List Bullet", "二つ目")]
    assert [c.text for c in d.tables[0]._cells] == ["項目", "内容", "a", "b"]
    assert d.paragraphs[1].runs[1].bold


def test_documents_have_a_size_limit_and_only_document_kinds_export(client, monkeypatch):
    item = client.post("/api/items", json={"kind": "文書", "title": "x"}).json()
    monkeypatch.setattr(db, "MAX_DOCUMENT_BYTES", 10)
    assert client.patch(f"/api/items/{item['id']}", json={"fields": {"本文": "あ" * 10}}).status_code == 400
    other = client.post("/api/items", json={"kind": "資料", "title": "y"}).json()
    assert client.get(f"/api/items/{other['id']}/document.docx").status_code == 400


def test_written_documents_appear_with_the_files_on_the_home_screen(client):
    file_item(client, PNG, "a.png", "写真")
    doc = client.post("/api/items", json={"kind": "文書", "title": "下書き", "fields": {"本文": "本文"}}).json()
    client.post(f"/api/items/{doc['id']}/opened")
    assert [i["title"] for i in client.get("/api/today").json()["reading"]] == ["下書き", "写真"]


def test_an_agent_writes_a_document_and_exports_it_from_the_cli(client, tmp_path, monkeypatch):
    import mcp_server

    item = client.post("/api/items", headers=AGENT, json={
        "kind": "文書", "title": "議事録", "fields": {"本文": "## 決まったこと\n\n1. 予算\n2. 日程"}}).json()

    def fake_req(method, path, body=None, query=None, raw=None, binary=False):
        r = client.request(method, path, json=body, params=query, headers=AGENT)
        assert r.status_code == 200, r.text
        return r.content if binary else r.json()

    monkeypatch.setattr(mcp_server, "_req", fake_req)
    out = tmp_path / "議事録.docx"
    mcp_server.export_document(item["id"], str(out))
    d = Document(str(out))
    assert [p.text for p in d.paragraphs] == ["決まったこと", "1. 予算", "2. 日程"]
