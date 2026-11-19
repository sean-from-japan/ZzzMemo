"""Tests for migrate_v1.py on a small old-schema database."""

import json
import sqlite3

import pytest

import migrate_v1
from core import db, storage


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    path = tmp_path / "qcatch.db"
    monkeypatch.setattr(db, "DB_FILE", path)
    conn = sqlite3.connect(path)
    conn.executescript(storage._SCHEMA)
    task = ("INSERT INTO tasks (id,text,status,category,tags,created_at,completed_at,due_date,"
            "importance,calendar_sync) VALUES (?,?,?,?,?,?,?,?,?,0)")
    conn.execute(task, ("t1", "書類を出す", "todo", "プライベート", '["事務手続き"]',
                        "2026-07-02T22:51:03.5", None, "2026-07-27T16:00:00", "high"))
    conn.execute(task, ("t2", "本棚", "wishlist", "買い物", "[]",
                        "2026-07-03T10:00:00", None, None, "medium"))
    conn.execute(task, ("t3", "捨てたもの", "trashed", None, "[]",
                        "2026-07-04T10:00:00", None, None, "medium"))
    conn.execute(task, ("t4", "終わったもの", "done", "謎の分類", "[]",
                        "2026-07-05T10:00:00", "2026-07-06T11:00:00.9", None, "low"))
    cl = "INSERT INTO checklists (id,name,due_date,items,parent_id,sort_order) VALUES (?,?,?,?,?,?)"
    conn.execute(cl, ("root", "持ち物", None, "[]", None, 0))
    conn.execute(cl, ("c1", "旅行", None,
                      json.dumps([{"text": "充電器", "done": False}, {"text": "タオル", "done": True}],
                                 ensure_ascii=False), "root", 0))
    conn.execute(cl, ("c2", "空のリスト", None, "[]", "root", 1))
    conn.execute("INSERT INTO diary_entries VALUES (?,?,?,?,?)",
                 ("2026-04-05", "一行目\n\n二行目", "[]", "2026-04-05T19:30:08.1", "2026-04-05T19:30:30"))
    conn.commit()
    conn.close()
    return path


def items_by_id():
    kinds = {k["id"]: k["name"] for k in db.list_kinds()}
    return {i["id"]: {**i, "kind": kinds[i["kind_id"]]} for i in db.list_items(status=None)}


def test_migration_maps_old_rows(old_db):
    assert migrate_v1.main(["migrate_v1.py"]) == 0
    items = items_by_id()

    assert set(items) == {"t1", "t2", "t4", "c1", "c2"}  # trashed task and empty parent skipped
    t1 = items["t1"]
    assert (t1["kind"], t1["status"], t1["due"]) == ("タスク", "open", "2026-07-27T16:00:00")
    assert t1["fields"] == {"重要度": "高", "分類": "プライベート"}
    assert t1["body"] == "タグ：事務手続き"
    assert t1["created_at"] == "2026-07-02T22:51:03"
    assert (items["t2"]["kind"], items["t2"]["fields"]) == ("欲しい物", {})
    t4 = items["t4"]
    assert (t4["status"], t4["completed_at"], t4["fields"]) == ("done", "2026-07-06T11:00:00", {"重要度": "低"})
    assert items["c1"]["title"] == "持ち物 › 旅行"
    assert items["c1"]["body"] == "- [ ] 充電器\n- [x] タオル"
    assert items["c2"]["body"] == ""

    entries = db.list_entries(day="2026-04-05")
    assert [e["body"] for e in entries] == ["一行目", "二行目"]
    assert all(e["state"] == "done" for e in entries)


def test_migration_runs_once_and_leaves_old_tables(old_db):
    assert migrate_v1.main(["migrate_v1.py"]) == 0
    assert migrate_v1.main(["migrate_v1.py"]) == 1
    conn = sqlite3.connect(old_db)
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM change_log WHERE actor='migration-v1'").fetchone()[0] == 7


def test_dry_run_writes_nothing(old_db):
    assert migrate_v1.main(["migrate_v1.py", "--dry-run"]) == 0
    assert items_by_id() == {}
    assert migrate_v1.main(["migrate_v1.py"]) == 0
