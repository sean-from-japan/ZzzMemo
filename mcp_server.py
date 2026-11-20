"""mcp_server.py — ZzzMemo tools for AI agents (MCP server and CLI)

The user writes raw lines ("entries") in the app; an agent later sorts them
into items of a kind (タスク, メモ, 欲しい物, ...). These tools wrap the v2 HTTP
API so an agent can do that sorting. The server holds no LLM: the thinking
happens in the agent.

Two ways to use the same tools:
  MCP server (needs `pip install mcp`):
      python mcp_server.py
  CLI, no extra packages (for slash commands, Codex, scripts):
      python mcp_server.py list                     # list tool names
      python mcp_server.py call list_unsorted '{}'
      python mcp_server.py call sort_entry '{"entry_id": "...", "kind": "タスク", "title": "..."}'

Configuration (environment variables):
  ZZZMEMO_BASE_URL   API base URL (default: https://zzzmemo.fly.dev)
  ZZZMEMO_API_KEY    the server's ZZZMEMO_API_KEY secret (sent as X-Api-Key)
  ZZZMEMO_ACTOR      name recorded in the change log (default: agent)

What an agent may do is decided by the server: it can add entries and items,
edit items, set an entry's state and undo those changes. A kind it adds stays
"proposed" until the user approves it in the app. Changing or deleting kinds
and rewriting what the user wrote are refused (HTTP 403).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

BASE_URL = os.environ.get("ZZZMEMO_BASE_URL", "https://zzzmemo.fly.dev").rstrip("/")
API_KEY = os.environ.get("ZZZMEMO_API_KEY", "")
ACTOR = os.environ.get("ZZZMEMO_ACTOR", "agent")

TOOLS = []


def tool(fn):
    TOOLS.append(fn)
    return fn


def _req(method: str, path: str, body: Optional[dict] = None, query: Optional[dict] = None) -> Any:
    """Call the ZzzMemo HTTP API and return parsed JSON."""
    url = BASE_URL + urllib.parse.quote(path)
    if query:
        q = {k: v for k, v in query.items() if v is not None}
        if q:
            url += "?" + urllib.parse.urlencode(q)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json", "X-Actor": ACTOR}
    if API_KEY:
        headers["X-Api-Key"] = API_KEY
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except (ValueError, AttributeError):
            pass
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"cannot reach {BASE_URL}: {e.reason}") from None


def _out(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)


def _entry(e: dict) -> dict:
    return {k: e[k] for k in ("id", "body", "day", "state", "created_at")}


def _item(i: dict, kinds: Optional[dict] = None) -> dict:
    out = {k: i[k] for k in ("id", "title", "status", "due", "fields", "body", "entry_id")}
    if kinds is not None:
        out["kind"] = kinds.get(i["kind_id"], i["kind_id"])
    return out


def _kind_names() -> dict:
    return {k["id"]: k["name"] for k in _req("GET", "/api/kinds")["kinds"]}


# ── reading ───────────────────────────────────────────────────────────────────


@tool
def list_kinds() -> str:
    """List kinds (categories) with their fields. Only kinds whose status is
    "active" can hold items; "proposed" ones wait for the user's approval."""
    kinds = _req("GET", "/api/kinds")["kinds"]
    return _out([{k: x[k] for k in ("id", "name", "status", "fields")} for x in kinds])


@tool
def list_unsorted(limit: int = 200) -> str:
    """Lines the user wrote that no agent has sorted yet (state "new"), oldest
    day first. Each line's `day` is the day it was written for: resolve words
    like 明日 relative to that day, not to today."""
    entries = _req("GET", "/api/entries", query={"state": "new", "limit": limit})["entries"]
    entries.sort(key=lambda e: (e["day"], e["created_at"]))
    return _out([_entry(e) for e in entries])


@tool
def list_entries(day: Optional[str] = None, state: Optional[str] = None, limit: int = 200) -> str:
    """Lines the user wrote, filtered by day (YYYY-MM-DD) and/or state
    (new / done / review)."""
    entries = _req("GET", "/api/entries", query={"day": day, "state": state, "limit": limit})["entries"]
    return _out([_entry(e) for e in entries])


@tool
def list_items(kind: Optional[str] = None, status: str = "open", q: Optional[str] = None,
               due_before: Optional[str] = None) -> str:
    """List items. kind: name or id. status: open / done / trashed / all.
    q: text search in title and body. due_before: ISO date or datetime."""
    items = _req("GET", "/api/items", query={"kind": kind, "status": status, "q": q,
                                             "due_before": due_before})["items"]
    names = _kind_names()
    return _out([_item(i, names) for i in items])


@tool
def get_today() -> str:
    """Today's lines, items due within 3 days, open tasks, and the number of
    kinds waiting for approval."""
    t = _req("GET", "/api/today")
    names = _kind_names()
    return _out({
        "day": t["day"],
        "entries": [_entry(e) for e in t["entries"]],
        "due_soon": [_item(i, names) for i in t["due_soon"]],
        "open_tasks": [_item(i, names) for i in t["open_tasks"]],
        "kinds_waiting_for_approval": t["review"],
    })


@tool
def recent_changes(limit: int = 20) -> str:
    """The change log, newest first. Use a log id with undo_change."""
    log = _req("GET", "/api/log", query={"limit": limit})["log"]
    out = []
    for entry in log:
        row = entry["after"] or entry["before"] or {}
        out.append({"id": entry["id"], "at": entry["at"], "actor": entry["actor"],
                    "table": entry["table_name"], "op": entry["op"],
                    "what": row.get("title") or row.get("body") or row.get("name"),
                    "undone": bool(entry["undone"])})
    return _out(out)


# ── writing ───────────────────────────────────────────────────────────────────


@tool
def sort_entry(entry_id: str, kind: str, title: str, due: Optional[str] = None,
               fields: Optional[dict] = None, body: str = "") -> str:
    """Turn one of the user's lines into an item and mark the line as sorted.
    kind: an active kind's name or id. due: ISO datetime such as
    2026-11-20T09:00:00 (use T00:00:00 when only the date is known), or omit.
    fields: values for the kind's fields only, e.g. {"重要度": "高"}."""
    item = _req("POST", "/api/items", {"kind": kind, "title": title, "due": due,
                                       "fields": fields or {}, "body": body, "entry_id": entry_id})
    _req("PATCH", f"/api/entries/{entry_id}", {"state": "done"})
    return _out({"item": _item(item), "entry": entry_id, "entry_state": "done"})


@tool
def mark_entry(entry_id: str, state: str) -> str:
    """Set a line's state without creating an item. state: "done" (nothing to
    file, e.g. a diary line), "review" (the user should decide), or "new"."""
    return _out(_entry(_req("PATCH", f"/api/entries/{entry_id}", {"state": state})))


@tool
def add_entries(text: str, day: Optional[str] = None) -> str:
    """Write lines on the user's behalf, one entry per line (day defaults to
    today). Use only when the user asks you to note something down."""
    return _out([_entry(e) for e in _req("POST", "/api/entries", {"body": text, "day": day})["entries"]])


@tool
def add_item(kind: str, title: str, due: Optional[str] = None,
             fields: Optional[dict] = None, body: str = "") -> str:
    """Create an item directly, not from a line (e.g. the user asked in chat)."""
    item = _req("POST", "/api/items", {"kind": kind, "title": title, "due": due,
                                       "fields": fields or {}, "body": body})
    return _out(_item(item))


@tool
def update_item(item_id: str, title: Optional[str] = None, status: Optional[str] = None,
                due: Optional[str] = None, clear_due: bool = False, kind: Optional[str] = None,
                fields: Optional[dict] = None, body: Optional[str] = None) -> str:
    """Change an item. status: open / done / trashed. clear_due=true removes
    the due date. Changing kind drops the old kind's field values."""
    changes = {k: v for k, v in {"title": title, "status": status, "due": due, "kind": kind,
                                 "fields": fields, "body": body}.items() if v is not None}
    if clear_due:
        changes["due"] = None
    if not changes:
        raise RuntimeError("nothing to change")
    return _out(_item(_req("PATCH", f"/api/items/{item_id}", changes)))


@tool
def propose_kind(name: str, fields: Optional[list] = None) -> str:
    """Propose a new kind. It stays "proposed" until the user approves it in
    the app, so mark the lines that need it as "review" in the meantime.
    fields: [{"name": "著者", "type": "text"}, {"name": "状態", "type":
    "choice", "options": ["未読", "読了"]}]; types: text, date, choice,
    number, bool."""
    k = _req("POST", "/api/kinds", {"name": name, "fields": fields or []})
    return _out({key: k[key] for key in ("id", "name", "status", "fields")})


@tool
def undo_change(log_id: int) -> str:
    """Undo one change from recent_changes. Refused if the row changed again
    afterwards (undo the later change first) or if it is a change to kinds."""
    return _out(_req("POST", f"/api/log/{log_id}/undo"))


# ── entry point ───────────────────────────────────────────────────────────────


def _cli(argv: list) -> int:
    by_name = {f.__name__: f for f in TOOLS}
    if argv[1] == "list":
        for f in TOOLS:
            print(f"{f.__name__}: {(f.__doc__ or '').strip().splitlines()[0]}")
        return 0
    if argv[1] != "call" or len(argv) < 3 or argv[2] not in by_name:
        print("usage: mcp_server.py [list | call <tool> '<json args>']", file=sys.stderr)
        return 2
    args = json.loads(argv[3]) if len(argv) > 3 else {}
    try:
        print(by_name[argv[2]](**args))
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(_cli(sys.argv))
    import functools

    try:  # mcp 2.x renamed FastMCP to MCPServer
        from mcp.server.mcpserver import MCPServer as Server
        from mcp.server.mcpserver.exceptions import ToolError
    except ImportError:
        from mcp.server.fastmcp import FastMCP as Server
        from mcp.server.fastmcp.exceptions import ToolError

    def _passing_errors(fn):
        # Without ToolError the client only sees "Error executing tool";
        # the agent needs the server's reason (e.g. which field value is wrong).
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except RuntimeError as e:
                raise ToolError(str(e)) from None
        return wrapper

    mcp = Server("ZzzMemo")
    for f in TOOLS:
        mcp.tool()(_passing_errors(f))
    mcp.run()
