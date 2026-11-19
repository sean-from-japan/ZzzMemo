"""Tests for the v2 API (core/db.py + web/routers/v2.py).

Each test gets its own empty SQLite file; nothing under data/ is touched.
Run: python -m pytest tests
"""

from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import db
from web.routers import v2

AGENT = {"X-Actor": "claude"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "test.db")
    db.init()
    app = FastAPI()
    app.include_router(v2.router)
    return TestClient(app)


def test_default_kinds_are_seeded(client):
    names = [k["name"] for k in client.get("/api/kinds").json()["kinds"]]
    assert names == ["タスク", "メモ", "欲しい物", "チェックリスト"]


def test_entries_split_lines_and_default_to_today(client):
    r = client.post("/api/entries", json={"body": "郵便局　荷物\n\n  レポート　下書き  "})
    assert r.status_code == 200
    bodies = [e["body"] for e in r.json()["entries"]]
    assert bodies == ["郵便局　荷物", "レポート　下書き"]
    today = client.get("/api/today").json()
    assert today["day"] == date.today().isoformat()
    assert len(today["entries"]) == 2
    assert all(e["state"] == "new" for e in today["entries"])


def test_empty_entry_and_bad_day_are_rejected(client):
    assert client.post("/api/entries", json={"body": " \n "}).status_code == 400
    assert client.post("/api/entries", json={"body": "x", "day": "11/19"}).status_code == 400


def test_agent_sorts_an_entry_into_a_task(client):
    entry = client.post("/api/entries", json={"body": "郵便局に荷物を出す 明日"}).json()["entries"][0]
    tomorrow = (date.today() + timedelta(days=1)).isoformat() + "T09:00:00"
    r = client.post("/api/items", headers=AGENT, json={
        "kind": "タスク", "title": "郵便局に荷物を出す", "due": tomorrow,
        "fields": {"重要度": "高"}, "entry_id": entry["id"],
    })
    assert r.status_code == 200, r.text
    item = r.json()
    assert client.patch(f"/api/entries/{entry['id']}", headers=AGENT,
                        json={"state": "done"}).status_code == 200

    today = client.get("/api/today").json()
    assert [i["id"] for i in today["due_soon"]] == [item["id"]]
    assert [i["id"] for i in today["open_tasks"]] == [item["id"]]
    assert client.get("/api/entries", params={"state": "new"}).json()["entries"] == []
    assert client.get("/api/log").json()["log"][0]["actor"] == "claude"


def test_item_field_values_are_checked_against_the_kind(client):
    def add(fields, kind="タスク"):
        return client.post("/api/items", json={"kind": kind, "title": "t", "fields": fields})

    assert add({"重要度": "最優先"}).status_code == 400
    assert add({"color": "red"}).status_code == 400
    assert add({}, kind="存在しない").status_code == 400
    assert add({"重要度": "低", "分類": "学習"}).status_code == 200
    assert client.post("/api/items", json={"kind": "メモ", "title": " "}).status_code == 400
    assert client.post("/api/items", json={"kind": "メモ", "title": "t", "due": "明日"}).status_code == 400


def test_completing_and_reopening_an_item(client):
    item = client.post("/api/items", json={"kind": "メモ", "title": "t"}).json()
    done = client.patch(f"/api/items/{item['id']}", json={"status": "done"}).json()
    assert done["completed_at"]
    assert client.get("/api/items", params={"kind": "メモ"}).json()["items"] == []
    assert len(client.get("/api/items", params={"status": "all"}).json()["items"]) == 1
    reopened = client.patch(f"/api/items/{item['id']}", json={"status": "open"}).json()
    assert reopened["completed_at"] is None


def test_changing_kind_drops_old_field_values(client):
    item = client.post("/api/items", json={
        "kind": "タスク", "title": "t", "fields": {"重要度": "高"}}).json()
    moved = client.patch(f"/api/items/{item['id']}", json={"kind": "メモ"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["fields"] == {}


def test_agent_kind_waits_for_approval(client):
    k = client.post("/api/kinds", headers=AGENT, json={
        "name": "読みたい本", "fields": [{"name": "著者", "type": "text"}]}).json()
    assert k["status"] == "proposed"
    assert client.get("/api/today").json()["review"] == 1
    # cannot hold items until approved
    assert client.post("/api/items", json={"kind": "読みたい本", "title": "x"}).status_code == 400
    # an agent cannot approve it, and cannot pass itself off as the user
    assert client.patch(f"/api/kinds/{k['id']}", headers=AGENT,
                        json={"status": "active"}).status_code == 403
    assert client.patch(f"/api/kinds/{k['id']}", headers={"X-Actor": "human"},
                        json={"status": "active"}).status_code == 200
    assert client.post("/api/items", json={
        "kind": "読みたい本", "title": "x", "fields": {"著者": "y"}}).status_code == 200


def test_agent_cannot_edit_entry_text_or_delete(client):
    entry = client.post("/api/entries", json={"body": "a"}).json()["entries"][0]
    assert client.patch(f"/api/entries/{entry['id']}", headers=AGENT,
                        json={"body": "b"}).status_code == 403
    assert client.delete(f"/api/entries/{entry['id']}", headers=AGENT).status_code == 403
    kind = client.get("/api/kinds").json()["kinds"][1]
    assert client.delete(f"/api/kinds/{kind['id']}", headers=AGENT).status_code == 403


def test_kind_definitions_are_validated(client):
    bad = [
        [{"name": "a", "type": "color"}],
        [{"name": "a", "type": "choice"}],
        [{"type": "text"}],
        [{"name": "a", "type": "text"}, {"name": "a", "type": "number"}],
    ]
    for fields in bad:
        assert client.post("/api/kinds", json={"name": "k", "fields": fields}).status_code == 400
    assert client.post("/api/kinds", json={"name": "タスク"}).status_code == 409


def test_kind_with_items_cannot_be_deleted(client):
    client.post("/api/items", json={"kind": "メモ", "title": "t"})
    memo = [k for k in client.get("/api/kinds").json()["kinds"] if k["name"] == "メモ"][0]
    assert client.delete(f"/api/kinds/{memo['id']}").status_code == 409


def test_undo_restores_and_refuses_when_row_changed_later(client):
    item = client.post("/api/items", json={"kind": "メモ", "title": "old"}).json()
    client.patch(f"/api/items/{item['id']}", headers=AGENT, json={"title": "new"})
    client.patch(f"/api/items/{item['id']}", headers=AGENT, json={"body": "later"})
    log = client.get("/api/log").json()["log"]
    title_change, body_change = log[1], log[0]

    assert client.post(f"/api/log/{title_change['id']}/undo").status_code == 409
    assert client.post(f"/api/log/{body_change['id']}/undo", headers=AGENT).status_code == 200
    assert client.post(f"/api/log/{title_change['id']}/undo", headers=AGENT).status_code == 200
    assert client.get(f"/api/items/{item['id']}").json()["title"] == "old"
    assert client.post(f"/api/log/{title_change['id']}/undo").status_code == 409


def test_undo_of_create_and_delete(client):
    entry = client.post("/api/entries", json={"body": "a"}).json()["entries"][0]
    client.delete(f"/api/entries/{entry['id']}")
    delete_log = client.get("/api/log").json()["log"][0]
    assert client.post(f"/api/log/{delete_log['id']}/undo").status_code == 200
    assert len(client.get("/api/entries").json()["entries"]) == 1

    item = client.post("/api/items", json={"kind": "メモ", "title": "t"}).json()
    create_log = client.get("/api/log").json()["log"][0]
    assert create_log["row_id"] == item["id"]
    assert client.post(f"/api/log/{create_log['id']}/undo").status_code == 200
    assert client.get(f"/api/items/{item['id']}").status_code == 404


def test_agent_cannot_undo_kind_changes(client):
    client.post("/api/kinds", json={"name": "k"})
    log = client.get("/api/log").json()["log"][0]
    assert client.post(f"/api/log/{log['id']}/undo", headers=AGENT).status_code == 403
    assert client.post(f"/api/log/{log['id']}/undo").status_code == 200
