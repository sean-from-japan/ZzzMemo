"""core/db.py — storage: entries, kinds, items and the change log.

Every write goes through `_write`, which records the before/after row in
`change_log` so that any change (by the user or an agent) can be undone.
There is no in-memory copy: each call reads SQLite.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

DB_FILE = Path(__file__).parent.parent / "data" / "qcatch.db"

ENTRY_STATES = {"new", "done", "review"}
KIND_STATUSES = {"active", "proposed", "rejected"}
ITEM_STATUSES = {"open", "done", "trashed"}
FIELD_TYPES = {"text", "date", "choice", "number", "bool", "file"}

# Files (reports, scripts) live next to the DB, not in it: the change log keeps
# whole rows, so a file inside a row would be copied into the log on every edit.
# A stored file is never rewritten; a new version is a new file id.
FILE_TYPES = {"html": "text/html; charset=utf-8", "pdf": "application/pdf"}
MAX_FILE_BYTES = 10 * 1024 * 1024
_FILE_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(html|pdf)$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    day TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'web',
    state TEXT NOT NULL DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS entries_day ON entries(day);

CREATE TABLE IF NOT EXISTS kinds (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    fields TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    kind_id TEXT NOT NULL REFERENCES kinds(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    fields TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'open',
    due TEXT,
    entry_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS items_kind ON items(kind_id, status);

-- When the user last opened an item's file. Not in change_log: opening is not
-- a change to undo, and it would bury real changes in the log.
CREATE TABLE IF NOT EXISTS opened (
    item_id TEXT PRIMARY KEY,
    at TEXT NOT NULL
);

-- What a page saved in its localStorage (answers in a workbook, checked
-- boxes). Not in change_log: a page saves on every keystroke.
CREATE TABLE IF NOT EXISTS page_state (
    item_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    table_name TEXT NOT NULL,
    row_id TEXT NOT NULL,
    op TEXT NOT NULL,
    before TEXT,
    after TEXT,
    undone INTEGER NOT NULL DEFAULT 0
);
"""

_IMPORTANCE = {"name": "重要度", "type": "choice", "options": ["高", "中", "低"]}
_CATEGORY = {
    "name": "分類",
    "type": "choice",
    "options": ["仕事", "プライベート", "買い物", "学習", "その他"],
}
DEFAULT_KINDS = [
    ("タスク", [_IMPORTANCE, _CATEGORY]),
    ("メモ", []),
    ("欲しい物", []),
    ("チェックリスト", []),
]

_JSON_COLS = {"fields", "proposed_fields"}
# Columns added after the first release. Rows in older log entries lack them,
# so undo fills these defaults in before comparing or restoring.
_ADDED_COLS = {
    "items": {"pinned": ("INTEGER NOT NULL DEFAULT 0", 0)},
    "kinds": {"proposed_fields": ("TEXT", None)},
}
_TABLES = {"entries", "kinds", "items"}


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


class Conflict(Exception):
    pass


class Forbidden(Exception):
    pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _tx():
    """One transaction: commit on success, roll back on error, always close."""
    conn = _conn()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in _JSON_COLS & d.keys():
        d[k] = json.loads(d[k]) if d[k] is not None else None
    return d


def _get(conn: sqlite3.Connection, table: str, row_id: str) -> dict:
    d = _row(conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone())
    if d is None:
        raise NotFound(f"{table}/{row_id}")
    return d


def _write(conn, actor: str, table: str, op: str, before: dict | None, after: dict | None) -> None:
    """Apply one insert/update/delete and record it in change_log."""
    assert table in _TABLES
    row = after if after is not None else before
    if after is not None:
        stored = {k: json.dumps(v, ensure_ascii=False) if k in _JSON_COLS else v for k, v in after.items()}
        cols = ",".join(stored)
        conn.execute(
            f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({','.join('?' * len(stored))})",
            tuple(stored.values()),
        )
    else:
        conn.execute(f"DELETE FROM {table} WHERE id=?", (before["id"],))
    conn.execute(
        "INSERT INTO change_log (at, actor, table_name, row_id, op, before, after) VALUES (?,?,?,?,?,?,?)",
        (
            _now(),
            actor,
            table,
            row["id"],
            op,
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(after, ensure_ascii=False) if after is not None else None,
        ),
    )


def init() -> None:
    with _tx() as conn:
        conn.executescript(_SCHEMA)
        for table, cols in _ADDED_COLS.items():
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            for col, (decl, _) in cols.items():
                if col not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        if conn.execute("SELECT COUNT(*) FROM kinds").fetchone()[0] == 0:
            for name, fields in DEFAULT_KINDS:
                _write(conn, "system", "kinds", "create", None, {
                    "id": str(uuid.uuid4()), "name": name, "fields": fields,
                    "status": "active", "created_at": _now(), "created_by": "system",
                })


# ── entries ───────────────────────────────────────────────────────────────────


def add_entries(text: str, actor: str, source: str = "web", day: str | None = None) -> list[dict]:
    """Store each non-empty line of `text` as one entry."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise Invalid("empty entry")
    day = day or date.today().isoformat()
    _check_day(day)
    out = []
    with _tx() as conn:
        for ln in lines:
            e = {"id": str(uuid.uuid4()), "body": ln, "day": day,
                 "created_at": _now(), "source": source, "state": "new"}
            _write(conn, actor, "entries", "create", None, e)
            out.append(e)
    return out


def list_entries(day: str | None = None, state: str | None = None, limit: int = 200) -> list[dict]:
    sql, args = "SELECT * FROM entries WHERE 1=1", []
    if day:
        sql += " AND day=?"
        args.append(day)
    if state:
        sql += " AND state=?"
        args.append(state)
    sql += " ORDER BY day DESC, created_at, rowid LIMIT ?"
    args.append(limit)
    with _tx() as conn:
        return [_row(r) for r in conn.execute(sql, args)]


def update_entry(entry_id: str, changes: dict, actor: str) -> dict:
    if "state" in changes and changes["state"] not in ENTRY_STATES:
        raise Invalid(f"state must be one of {sorted(ENTRY_STATES)}")
    if "body" in changes and not str(changes["body"]).strip():
        raise Invalid("empty entry")
    if "day" in changes:
        _check_day(changes["day"])
    with _tx() as conn:
        before = _get(conn, "entries", entry_id)
        after = {**before, **changes}
        _write(conn, actor, "entries", "update", before, after)
    return after


def delete_entry(entry_id: str, actor: str) -> None:
    with _tx() as conn:
        _write(conn, actor, "entries", "delete", _get(conn, "entries", entry_id), None)


# ── kinds ─────────────────────────────────────────────────────────────────────


def _check_fields_def(fields: list) -> None:
    names = set()
    for f in fields:
        if not isinstance(f, dict) or not f.get("name"):
            raise Invalid("each field needs a name")
        if f.get("type") not in FIELD_TYPES:
            raise Invalid(f"field type must be one of {sorted(FIELD_TYPES)}")
        if f["type"] == "choice" and not f.get("options"):
            raise Invalid(f"choice field '{f['name']}' needs options")
        if f["name"] in names:
            raise Invalid(f"duplicate field '{f['name']}'")
        names.add(f["name"])


def list_kinds(status: str | None = None) -> list[dict]:
    with _tx() as conn:
        if status:
            rows = conn.execute("SELECT * FROM kinds WHERE status=? ORDER BY created_at", (status,))
        else:
            rows = conn.execute("SELECT * FROM kinds ORDER BY created_at")
        return [_row(r) for r in rows]


def add_kind(name: str, fields: list, actor: str, status: str) -> dict:
    name = name.strip()
    if not name:
        raise Invalid("kind name is empty")
    _check_fields_def(fields)
    k = {"id": str(uuid.uuid4()), "name": name, "fields": fields,
         "status": status, "created_at": _now(), "created_by": actor, "proposed_fields": None}
    with _tx() as conn:
        if conn.execute("SELECT 1 FROM kinds WHERE name=?", (name,)).fetchone():
            raise Conflict(f"kind '{name}' already exists")
        _write(conn, actor, "kinds", "create", None, k)
    return k


def update_kind(kind_id: str, changes: dict, actor: str) -> dict:
    if "status" in changes and changes["status"] not in KIND_STATUSES:
        raise Invalid(f"status must be one of {sorted(KIND_STATUSES)}")
    if "fields" in changes:
        _check_fields_def(changes["fields"])
    if changes.get("proposed_fields") is not None:
        _check_fields_def(changes["proposed_fields"])
    with _tx() as conn:
        before = _get(conn, "kinds", kind_id)
        after = {**before, **changes}
        _write(conn, actor, "kinds", "update", before, after)
    return after


def delete_kind(kind_id: str, actor: str) -> None:
    with _tx() as conn:
        before = _get(conn, "kinds", kind_id)
        if conn.execute("SELECT 1 FROM items WHERE kind_id=? LIMIT 1", (kind_id,)).fetchone():
            raise Conflict("kind still has items")
        _write(conn, actor, "kinds", "delete", before, None)


# ── items ─────────────────────────────────────────────────────────────────────


def _check_day(s: str) -> None:
    try:
        date.fromisoformat(s)
    except (TypeError, ValueError):
        raise Invalid(f"not a date: {s!r}")


def _check_due(s: str | None) -> None:
    if s is None:
        return
    try:
        datetime.fromisoformat(s)
    except (TypeError, ValueError):
        raise Invalid(f"not a date/time: {s!r}")


def _check_values(kind: dict, values: dict) -> None:
    defs = {f["name"]: f for f in kind["fields"]}
    for name, v in values.items():
        f = defs.get(name)
        if f is None:
            raise Invalid(f"kind '{kind['name']}' has no field '{name}'")
        if v is None:
            continue
        t = f["type"]
        ok = (
            (t == "text" and isinstance(v, str))
            or (t == "number" and isinstance(v, (int, float)) and not isinstance(v, bool))
            or (t == "bool" and isinstance(v, bool))
            or (t == "choice" and v in f["options"])
        )
        if t == "date" and isinstance(v, str):
            _check_day(v)
            ok = True
        if t == "file" and isinstance(v, str):
            file_path(v)
            ok = True
        if not ok:
            raise Invalid(f"bad value for field '{name}' ({t}): {v!r}")


def _resolve_kind(conn, kind: str) -> dict:
    """Accept a kind id or a kind name; only active kinds can hold items."""
    r = conn.execute("SELECT * FROM kinds WHERE id=? OR name=?", (kind, kind)).fetchone()
    if r is None:
        raise Invalid(f"unknown kind '{kind}'")
    k = _row(r)
    if k["status"] != "active":
        raise Invalid(f"kind '{k['name']}' is {k['status']}, not active")
    return k


def list_items(kind: str | None = None, status: str | None = "open", q: str | None = None,
               due_before: str | None = None, limit: int = 500) -> list[dict]:
    sql = "SELECT items.* FROM items JOIN kinds ON kinds.id=items.kind_id WHERE 1=1"
    args: list = []
    if kind:
        sql += " AND (kinds.id=? OR kinds.name=?)"
        args += [kind, kind]
    if status:
        sql += " AND items.status=?"
        args.append(status)
    if q:
        sql += " AND (items.title LIKE ? OR items.body LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    if due_before:
        sql += " AND items.due IS NOT NULL AND items.due < ?"
        args.append(due_before)
    sql += " ORDER BY items.due IS NULL, items.due, items.created_at LIMIT ?"
    args.append(limit)
    with _tx() as conn:
        return [_row(r) for r in conn.execute(sql, args)]


def get_item(item_id: str) -> dict:
    with _tx() as conn:
        return _get(conn, "items", item_id)


def add_item(data: dict, actor: str) -> dict:
    title = (data.get("title") or "").strip()
    if not title:
        raise Invalid("title is empty")
    values = data.get("fields") or {}
    _check_due(data.get("due"))
    with _tx() as conn:
        kind = _resolve_kind(conn, data.get("kind") or "")
        _check_values(kind, values)
        if data.get("entry_id"):
            _get(conn, "entries", data["entry_id"])
        now = _now()
        it = {"id": str(uuid.uuid4()), "kind_id": kind["id"], "title": title,
              "body": data.get("body") or "", "fields": values, "status": "open",
              "due": data.get("due"), "entry_id": data.get("entry_id"),
              "created_at": now, "updated_at": now, "completed_at": None, "pinned": 0}
        _write(conn, actor, "items", "create", None, it)
    return it


def update_item(item_id: str, changes: dict, actor: str) -> dict:
    if "status" in changes and changes["status"] not in ITEM_STATUSES:
        raise Invalid(f"status must be one of {sorted(ITEM_STATUSES)}")
    if "title" in changes and not str(changes["title"]).strip():
        raise Invalid("title is empty")
    _check_due(changes.get("due"))
    if "pinned" in changes:
        changes = {**changes, "pinned": int(bool(changes["pinned"]))}
    with _tx() as conn:
        before = _get(conn, "items", item_id)
        after = {**before, **{k: v for k, v in changes.items() if k != "kind"}}
        if "kind" in changes:
            after["kind_id"] = _resolve_kind(conn, changes["kind"])["id"]
        kind = _get(conn, "kinds", after["kind_id"])
        if "kind" in changes and after["kind_id"] != before["kind_id"]:
            # values of the old kind's fields do not carry over; the log keeps them
            after["fields"] = changes.get("fields") or {}
        elif "fields" in changes:
            after["fields"] = {**before["fields"], **changes["fields"]}
        _check_values(kind, after["fields"])
        if after["status"] == "done" and before["status"] != "done":
            after["completed_at"] = _now()
        elif after["status"] != "done":
            after["completed_at"] = None
        after["updated_at"] = _now()
        _write(conn, actor, "items", "update", before, after)
    return after


# ── files ─────────────────────────────────────────────────────────────────────


def _files_dir() -> Path:
    return DB_FILE.parent / "files"


def save_file(data: bytes, filename: str) -> str:
    """Store an HTML or PDF file and return its new id (uuid + extension)."""
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext == "htm":
        ext = "html"
    if ext not in FILE_TYPES:
        raise Invalid(f"only {', '.join(sorted(FILE_TYPES))} files are accepted")
    if not data:
        raise Invalid("empty file")
    if len(data) > MAX_FILE_BYTES:
        raise Invalid(f"file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if ext == "pdf" and not data.startswith(b"%PDF-"):
        raise Invalid("not a PDF file")
    if ext == "html" and not head.startswith((b"<!doctype html", b"<html")):
        raise Invalid("not an HTML document (must start with <!DOCTYPE html> or <html>)")
    file_id = f"{uuid.uuid4()}.{ext}"
    d = _files_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (file_id + ".part")
    tmp.write_bytes(data)
    tmp.rename(d / file_id)
    return file_id


def file_path(file_id: str) -> Path:
    if not isinstance(file_id, str) or not _FILE_ID.match(file_id):
        raise Invalid(f"not a file id: {file_id!r}")
    p = _files_dir() / file_id
    if not p.is_file():
        raise NotFound(f"files/{file_id}")
    return p


# ── today / log ───────────────────────────────────────────────────────────────


def mark_opened(item_id: str) -> None:
    with _tx() as conn:
        _get(conn, "items", item_id)
        conn.execute("INSERT OR REPLACE INTO opened (item_id, at) VALUES (?, ?)", (item_id, _now()))


MAX_PAGE_STATE_BYTES = 1024 * 1024


def item_for_file(file_id: str) -> str | None:
    """The id of an item whose field holds this file, or None."""
    with _tx() as conn:
        for r in conn.execute("SELECT id, fields FROM items WHERE fields LIKE ? ORDER BY status='open' DESC",
                              (f'%"{file_id}"%',)):
            if file_id in json.loads(r["fields"]).values():
                return r["id"]
    return None


def get_page_state(item_id: str) -> dict:
    with _tx() as conn:
        r = conn.execute("SELECT data FROM page_state WHERE item_id=?", (item_id,)).fetchone()
    return json.loads(r["data"]) if r else {}


def set_page_state(item_id: str, data: dict) -> None:
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise Invalid("page state keys and values must be strings")
    text = json.dumps(data, ensure_ascii=False)
    if len(text.encode()) > MAX_PAGE_STATE_BYTES:
        raise Invalid("page state is larger than 1 MB")
    with _tx() as conn:
        _get(conn, "items", item_id)
        conn.execute("INSERT OR REPLACE INTO page_state (item_id, data, at) VALUES (?, ?, ?)",
                     (item_id, text, _now()))


def reading(limit: int = 6) -> list[dict]:
    """Open items that hold a file: pinned first, then the most recently
    opened, then the newest. Each row carries `opened_at` (or None)."""
    file_field = {k["id"]: f["name"] for k in list_kinds("active") for f in k["fields"] if f["type"] == "file"}
    ids = list(file_field)
    if not ids:
        return []
    with _tx() as conn:
        rows = conn.execute(
            "SELECT items.*, opened.at AS opened_at FROM items LEFT JOIN opened ON opened.item_id=items.id"
            f" WHERE items.status='open' AND items.kind_id IN ({','.join('?' * len(ids))})"
            " ORDER BY items.pinned DESC, opened.at IS NULL, opened.at DESC, items.created_at DESC",
            ids,
        )
        items = [_row(r) for r in rows]
    return [i for i in items if i["fields"].get(file_field[i["kind_id"]])][:limit]


def today(days_ahead: int = 3) -> dict:
    d = date.today()
    horizon = (d + timedelta(days=days_ahead + 1)).isoformat()
    return {
        "day": d.isoformat(),
        "entries": list_entries(day=d.isoformat()),
        "due_soon": list_items(status="open", due_before=horizon),
        "open_tasks": list_items(kind="タスク", status="open"),
        "reading": reading(),
        "review": len(list_kinds(status="proposed"))
        + sum(1 for k in list_kinds("active") if k["proposed_fields"] is not None),
    }


def list_log(limit: int = 100) -> list[dict]:
    with _tx() as conn:
        rows = conn.execute("SELECT * FROM change_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["before"] = json.loads(d["before"]) if d["before"] else None
        d["after"] = json.loads(d["after"]) if d["after"] else None
        out.append(d)
    return out


def undo(log_id: int, actor: str, by_agent: bool = False) -> dict:
    """Revert one change, provided the row has not been changed since."""
    with _tx() as conn:
        r = conn.execute("SELECT * FROM change_log WHERE id=?", (log_id,)).fetchone()
        if r is None:
            raise NotFound(f"log/{log_id}")
        if r["undone"]:
            raise Conflict("already undone")
        if r["table_name"] not in _TABLES:
            raise Invalid("cannot undo this change")
        if by_agent and r["table_name"] == "kinds":
            raise Forbidden("only the user can undo a change to kinds")
        table, row_id = r["table_name"], r["row_id"]
        before = json.loads(r["before"]) if r["before"] else None
        after = json.loads(r["after"]) if r["after"] else None
        defaults = {c: d for c, (_, d) in _ADDED_COLS.get(table, {}).items()}
        before = {**defaults, **before} if before is not None else None
        after = {**defaults, **after} if after is not None else None
        current = _row(conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone())
        if current != after:
            raise Conflict("the row was changed after this; undo the later change first")
        if table == "kinds" and before is None and conn.execute(
            "SELECT 1 FROM items WHERE kind_id=? LIMIT 1", (row_id,)
        ).fetchone():
            raise Conflict("kind still has items")
        _write(conn, actor, table, "undo", current, before)
        conn.execute("UPDATE change_log SET undone=1 WHERE id=?", (log_id,))
    return {"undone": log_id, "table": table, "row_id": row_id}
