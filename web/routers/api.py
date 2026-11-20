"""web/routers/api.py — /api/entries, /api/kinds, /api/items, /api/files, /files,
/api/today, /api/log

Who is calling decides what is allowed:
- A request with an `X-Actor` header or the internal API key is an agent.
  Agents may add entries and items, edit items and set an entry's state.
  A kind an agent adds starts as `proposed` and waits for the user's approval.
- Anything else is the user (browser session), who may do everything.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from core import db
from web import deps

router = APIRouter()


def _actor(request: Request) -> str:
    name = request.headers.get("X-Actor", "").strip()
    if name and name not in ("human", "system"):
        return name[:40]
    key = request.headers.get("X-Api-Key", "")
    if deps.INTERNAL_API_KEY and key and secrets.compare_digest(key, deps.INTERNAL_API_KEY):
        return "api"
    return "human"


def _user_only(actor: str) -> None:
    if actor != "human":
        raise HTTPException(403, "this change needs the user's approval in the app")


def _call(fn, *args):
    try:
        return fn(*args)
    except db.NotFound as e:
        raise HTTPException(404, f"not found: {e}")
    except db.Invalid as e:
        raise HTTPException(400, str(e))
    except db.Conflict as e:
        raise HTTPException(409, str(e))
    except db.Forbidden as e:
        raise HTTPException(403, str(e))


# ── schemas ───────────────────────────────────────────────────────────────────


class EntryIn(BaseModel):
    body: str
    day: Optional[str] = None


class EntryPatch(BaseModel):
    body: Optional[str] = None
    day: Optional[str] = None
    state: Optional[Literal["new", "done", "review"]] = None


class KindIn(BaseModel):
    name: str
    fields: list[dict[str, Any]] = []


class KindPatch(BaseModel):
    name: Optional[str] = None
    fields: Optional[list[dict[str, Any]]] = None
    status: Optional[Literal["active", "proposed", "rejected"]] = None


class ItemIn(BaseModel):
    kind: str
    title: str
    body: str = ""
    fields: dict[str, Any] = {}
    due: Optional[str] = None
    entry_id: Optional[str] = None


class ItemPatch(BaseModel):
    kind: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    fields: Optional[dict[str, Any]] = None
    due: Optional[str] = None
    status: Optional[Literal["open", "done", "trashed"]] = None


def _changes(model: BaseModel) -> dict:
    """Fields the caller actually sent; an explicit null (e.g. due) is kept."""
    return model.model_dump(exclude_unset=True)


# ── entries ───────────────────────────────────────────────────────────────────


@router.get("/api/entries")
def get_entries(day: Optional[str] = None, state: Optional[str] = None, limit: int = 200):
    return {"entries": _call(db.list_entries, day, state, limit)}


@router.post("/api/entries")
def post_entries(body: EntryIn, request: Request):
    actor = _actor(request)
    source = "web" if actor == "human" else "api"
    return {"entries": _call(db.add_entries, body.body, actor, source, body.day)}


@router.patch("/api/entries/{entry_id}")
def patch_entry(entry_id: str, body: EntryPatch, request: Request):
    actor = _actor(request)
    changes = _changes(body)
    if set(changes) - {"state"}:
        _user_only(actor)
    return _call(db.update_entry, entry_id, changes, actor)


@router.delete("/api/entries/{entry_id}")
def delete_entry(entry_id: str, request: Request):
    actor = _actor(request)
    _user_only(actor)
    _call(db.delete_entry, entry_id, actor)
    return {"ok": True}


# ── kinds ─────────────────────────────────────────────────────────────────────


@router.get("/api/kinds")
def get_kinds(status: Optional[str] = None):
    return {"kinds": _call(db.list_kinds, status)}


@router.post("/api/kinds")
def post_kind(body: KindIn, request: Request):
    actor = _actor(request)
    status = "active" if actor == "human" else "proposed"
    return _call(db.add_kind, body.name, body.fields, actor, status)


@router.patch("/api/kinds/{kind_id}")
def patch_kind(kind_id: str, body: KindPatch, request: Request):
    actor = _actor(request)
    _user_only(actor)
    return _call(db.update_kind, kind_id, _changes(body), actor)


@router.delete("/api/kinds/{kind_id}")
def delete_kind(kind_id: str, request: Request):
    actor = _actor(request)
    _user_only(actor)
    _call(db.delete_kind, kind_id, actor)
    return {"ok": True}


# ── items ─────────────────────────────────────────────────────────────────────


@router.get("/api/items")
def get_items(kind: Optional[str] = None, status: Optional[str] = "open",
              q: Optional[str] = None, due_before: Optional[str] = None, limit: int = 500):
    # status=all lists every status
    st = None if status == "all" else status
    return {"items": _call(db.list_items, kind, st, q, due_before, limit)}


@router.get("/api/items/{item_id}")
def get_item(item_id: str):
    return _call(db.get_item, item_id)


@router.post("/api/items")
def post_item(body: ItemIn, request: Request):
    return _call(db.add_item, body.model_dump(), _actor(request))


@router.patch("/api/items/{item_id}")
def patch_item(item_id: str, body: ItemPatch, request: Request):
    return _call(db.update_item, item_id, _changes(body), _actor(request))


# ── files ─────────────────────────────────────────────────────────────────────

# A stored HTML page runs in a sandbox with no origin: its scripts work (step
# buttons, slide paging) but cannot read the session cookie or call /api.
_HTML_HEADERS = {
    "Content-Security-Policy": "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, no-cache",
}
# Pages cut into `.slide` blocks get previous/next buttons, as in the class app.
# Inlined: a sandboxed page has no origin, and some browsers block its requests.
_PAGER = (b"<script>" + (Path(__file__).parent.parent / "static" / "slide_paging.js").read_bytes()
          + b"</script>")


@router.post("/api/files")
async def post_file(request: Request, name: str):
    # The body is the raw file, so agents can upload without multipart encoding.
    size = int(request.headers.get("content-length") or 0)
    if size > db.MAX_FILE_BYTES:
        raise HTTPException(400, f"file is larger than {db.MAX_FILE_BYTES // (1024 * 1024)} MB")
    file_id = _call(db.save_file, await request.body(), name)
    return {"id": file_id, "url": f"/files/{file_id}"}


@router.get("/files/{file_id}")
def get_file(file_id: str):
    path = _call(db.file_path, file_id)
    ext = path.suffix.lstrip(".")
    if ext == "pdf":
        return FileResponse(str(path), media_type=db.FILE_TYPES[ext],
                            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-cache"})
    data = path.read_bytes()
    i = data.lower().rfind(b"</body>")
    data = data[:i] + _PAGER + data[i:] if i >= 0 else data + _PAGER
    return Response(data, media_type=db.FILE_TYPES[ext], headers=_HTML_HEADERS)


# ── today / log ───────────────────────────────────────────────────────────────


@router.get("/api/today")
def get_today():
    return db.today()


@router.get("/api/log")
def get_log(limit: int = 100):
    return {"log": db.list_log(limit)}


@router.post("/api/log/{log_id}/undo")
def post_undo(log_id: int, request: Request):
    actor = _actor(request)
    return _call(db.undo, log_id, actor, actor != "human")
