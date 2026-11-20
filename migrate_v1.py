"""migrate_v1.py — copy the old (v1) data into the v2 tables, once.

Reads the old tables in the same SQLite file and writes v2 rows through
core.db, so every migrated row is in the change log (actor "migration-v1").
The old tables are left untouched.

  tasks       todo / inbox / longterm -> タスク (open), done -> タスク (done),
              wishlist -> 欲しい物, trashed -> not copied.
              Same id as before, so Google sync can be matched up later.
              importance -> 重要度 (高/中/低), category -> 分類, tags -> body.
  checklists  every list that has items or has no sub-lists -> チェックリスト,
              titled with its path (持ち物 › 授業 › 授業＋買い物), items as
              "- [ ]" / "- [x]" lines. Empty parent folders are not copied.
  diary       each line of each day -> an entry of that day, state "done".
  archived_tasks, blog, flashcards, recurring rules: not copied.

Usage (on the server: fly ssh console -C "python migrate_v1.py --dry-run"):
  python migrate_v1.py --dry-run   # show what would be written
  python migrate_v1.py             # write it
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid

from core import db

ACTOR = "migration-v1"
IMPORTANCE = {"high": "高", "medium": "中", "low": "低"}
CATEGORIES = {"仕事", "プライベート", "買い物", "学習", "その他"}


def _ts(s: str | None) -> str | None:
    return s[:19] if s else None


def _tables(conn) -> set:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def plan(conn: sqlite3.Connection, kinds: dict) -> list[tuple[str, dict]]:
    """Return the v2 rows to insert as (table, row) pairs."""
    rows: list[tuple[str, dict]] = []
    old = _tables(conn)

    if "tasks" in old:
        for t in conn.execute("SELECT * FROM tasks ORDER BY created_at"):
            if t["status"] == "trashed":
                continue
            kind = "欲しい物" if t["status"] == "wishlist" else "タスク"
            fields = {}
            if kind == "タスク":
                if t["importance"] in IMPORTANCE:
                    fields["重要度"] = IMPORTANCE[t["importance"]]
                if t["category"] in CATEGORIES:
                    fields["分類"] = t["category"]
            tags = json.loads(t["tags"] or "[]")
            done = t["status"] == "done"
            rows.append(("items", {
                "id": t["id"], "kind_id": kinds[kind], "title": t["text"],
                "body": f"タグ：{'、'.join(tags)}" if tags else "",
                "fields": fields, "status": "done" if done else "open",
                "due": _ts(t["due_date"]), "entry_id": None,
                "created_at": _ts(t["created_at"]), "updated_at": _ts(t["created_at"]),
                "completed_at": _ts(t["completed_at"]) if done else None,
            }))

    if "checklists" in old:
        lists = {c["id"]: dict(c) for c in conn.execute("SELECT * FROM checklists")}
        parents = {c["parent_id"] for c in lists.values() if c["parent_id"]}

        def path(c: dict) -> list[str]:
            seen, names = set(), []
            while c and c["id"] not in seen:
                seen.add(c["id"])
                names.append(c["name"])
                c = lists.get(c["parent_id"])
            return names[::-1]

        for c in sorted(lists.values(), key=lambda c: (path(c), c["sort_order"])):
            items = json.loads(c["items"] or "[]")
            if not items and c["id"] in parents:
                continue
            body = "\n".join(f"- [{'x' if i.get('done') else ' '}] {i['text']}" for i in items)
            rows.append(("items", {
                "id": c["id"], "kind_id": kinds["チェックリスト"], "title": " › ".join(path(c)),
                "body": body, "fields": {}, "status": "open", "due": _ts(c["due_date"]),
                "entry_id": None, "created_at": db._now(), "updated_at": db._now(),
                "completed_at": None,
            }))

    if "diary_entries" in old:
        for d in conn.execute("SELECT * FROM diary_entries ORDER BY date_str"):
            for line in d["content"].splitlines():
                if line.strip():
                    rows.append(("entries", {
                        "id": str(uuid.uuid4()), "body": line.strip(), "day": d["date_str"],
                        "created_at": _ts(d["created_at"]), "source": "migration", "state": "done",
                    }))
    return rows


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    db.init()
    with db._tx() as conn:
        if conn.execute("SELECT 1 FROM change_log WHERE actor=? LIMIT 1", (ACTOR,)).fetchone():
            print("already migrated (change_log has rows by migration-v1); nothing to do")
            return 1
        kinds = {k["name"]: k["id"] for k in map(dict, conn.execute("SELECT * FROM kinds"))}
        missing = {"タスク", "欲しい物", "チェックリスト"} - kinds.keys()
        if missing:
            print(f"missing kinds: {sorted(missing)}")
            return 1
        rows = plan(conn, kinds)
        if not dry:
            for table, row in rows:
                db._write(conn, ACTOR, table, "create", None, row)

    names = {v: k for k, v in kinds.items()}
    print(("would write" if dry else "wrote") + f" {len(rows)} rows")
    for table, row in rows:
        if table == "items":
            extra = f" due={row['due']}" if row["due"] else ""
            print(f"  {names[row['kind_id']]}\t{row['status']}\t{row['title']}{extra}")
        else:
            print(f"  メモ行\t{row['day']}\t{row['body']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
