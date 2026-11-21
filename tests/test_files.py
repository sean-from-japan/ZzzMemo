"""Tests for stored files (HTML/PDF) and the "file" field type.

Each test gets its own temporary DB and files folder; nothing under data/ is
touched. The documents are made-up samples.
"""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import db
from web.routers import api

AGENT = {"X-Actor": "claude"}
# a well-formed file id that was never uploaded
MISSING = f"{uuid.UUID(int=0)}.html"
HTML = b"<!DOCTYPE html><html><head><title>t</title></head><body><p>sample</p></body></html>"
PDF = b"%PDF-1.4\n%sample\n"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "test.db")
    db.init()
    app = FastAPI()
    app.include_router(api.router)
    c = TestClient(app)
    # the user approves a kind with a file field, as in the app
    k = c.post("/api/kinds", headers=AGENT, json={"name": "資料", "fields": [
        {"name": "ファイル", "type": "file"},
        {"name": "分類", "type": "choice", "options": ["調べごと", "英語"]},
    ]}).json()
    assert k["status"] == "proposed"
    assert c.patch(f"/api/kinds/{k['id']}", json={"status": "active"}).status_code == 200
    return c


def upload(client, data, name):
    return client.post("/api/files", params={"name": name}, content=data, headers=AGENT)


def test_agent_uploads_html_and_files_it(client, tmp_path):
    r = upload(client, HTML, "report.html")
    assert r.status_code == 200, r.text
    file_id = r.json()["id"]
    assert file_id.endswith(".html")
    assert (tmp_path / "files" / file_id).read_bytes() == HTML
    item = client.post("/api/items", headers=AGENT, json={
        "kind": "資料", "title": "椅子の比較", "fields": {"ファイル": file_id, "分類": "調べごと"},
    })
    assert item.status_code == 200, item.text
    # the change log holds the file id, not the file
    assert client.get("/api/log").json()["log"][0]["after"]["fields"]["ファイル"] == file_id


def test_html_is_served_sandboxed_with_the_slide_pager(client):
    file_id = upload(client, HTML, "report.html").json()["id"]
    r = client.get(f"/files/{file_id}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    csp = r.headers["content-security-policy"]
    assert csp.startswith("sandbox") and "allow-scripts" in csp and "allow-same-origin" not in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.content.startswith(HTML[:-len(b"</body></html>")] + b"<script>")
    assert b'querySelectorAll(".slide")' in r.content
    assert r.content.endswith(b"</script></body></html>")


def test_pdf_is_served_as_is(client):
    file_id = upload(client, PDF, "script.PDF").json()["id"]
    r = client.get(f"/files/{file_id}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content == PDF


def test_wrong_files_are_refused(client, monkeypatch):
    assert upload(client, HTML, "notes.txt").status_code == 400
    assert upload(client, b"", "a.html").status_code == 400
    assert upload(client, b"hello", "a.html").status_code == 400
    assert upload(client, HTML, "a.pdf").status_code == 400
    monkeypatch.setattr(db, "MAX_FILE_BYTES", 10)
    assert upload(client, HTML, "a.html").status_code == 400


def test_file_ids_are_checked(client):
    assert client.get("/files/..%2Ftest.db").status_code in (400, 404)
    assert client.get(f"/files/{MISSING}").status_code == 404
    bad = client.post("/api/items", json={"kind": "資料", "title": "x",
                                          "fields": {"ファイル": MISSING}})
    assert bad.status_code == 404
    assert client.post("/api/items", json={"kind": "資料", "title": "x",
                                           "fields": {"ファイル": "../test.db"}}).status_code == 400


def test_replacing_a_file_can_be_undone(client):
    first = upload(client, HTML, "a.html").json()["id"]
    second = upload(client, HTML, "a.html").json()["id"]
    item = client.post("/api/items", headers=AGENT, json={
        "kind": "資料", "title": "英語のスクリプト", "fields": {"ファイル": first}}).json()
    client.patch(f"/api/items/{item['id']}", headers=AGENT, json={"fields": {"ファイル": second}})
    log_id = client.get("/api/log").json()["log"][0]["id"]
    assert client.post(f"/api/log/{log_id}/undo", headers=AGENT).status_code == 200
    assert client.get(f"/api/items/{item['id']}").json()["fields"]["ファイル"] == first
    # both versions are still there
    assert client.get(f"/files/{first}").status_code == 200
    assert client.get(f"/files/{second}").status_code == 200


def test_add_file_folds_local_assets_into_the_page(tmp_path):
    import mcp_server

    (tmp_path / "lib" / "fonts").mkdir(parents=True)
    (tmp_path / "lib" / "fonts" / "a.woff2").write_bytes(b"w2")
    (tmp_path / "lib" / "s.css").write_text(
        '@font-face{src:url(fonts/a.woff2) format("woff2"),url(fonts/a.woff) format("woff")}', encoding="utf-8")
    (tmp_path / "lib" / "step.js").write_text('document.title = "</script>";', encoding="utf-8")
    (tmp_path / "fig.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    page = ('<!DOCTYPE html><html><head><link rel="stylesheet" href="lib/s.css">'
            '<script defer src="lib/step.js"></script>'
            '<script src="https://cdn.example/x.js"></script></head>'
            '<body><img src="fig.svg"><img src="missing.png"><a href="#s2">2</a></body></html>')
    out = mcp_server._inline_assets(page, str(tmp_path))
    assert 'href="lib/s.css"' not in out and "data:font/woff2;base64," in out
    assert "a.woff" not in out  # the woff fallback is dropped when woff2 exists
    assert '<script defer >document.title = "<\\/script>";</script>' in out
    assert 'src="https://cdn.example/x.js"' in out  # remote and missing files stay as they are
    assert 'src="data:image/svg+xml;base64,' in out and 'src="missing.png"' in out
    assert 'href="#s2"' in out
