**English** | [日本語](README.ja.md)

# ZzzMemo

**A personal notebook built for AI agents.** You write one line at a time. Later, an AI agent (Claude Code, Codex, or any MCP client) reads the unsorted lines and files them as tasks, notes or wish-list items. The app itself contains no LLM.

![The Today screen: a large capture box, today's lines with their sorting state, and tasks with due dates](docs/screenshot.png)

---

## Why it works this way

The first version of ZzzMemo had Gemini built in and asked for a category, due date and priority for every task. Writing things down was easy; filing them was the chore that got skipped. Version 2 splits the work:

- **You** only write lines, as fast as in a paper notebook. No fields to fill in.
- **An agent** sorts them later, in a batch, when you ask it to (for example a `/zzz` slash command in Claude Code). Nothing needs an LLM in real time, so the server holds no LLM API key.
- **Kinds are data, not code.** タスク (tasks), メモ (notes), 欲しい物 (wish list) and チェックリスト exist by default. An agent can propose a new kind with its own fields; it becomes usable after you approve it in the app. The screens are generated from the kind definitions, so adding a kind changes no code.
- **Agents can leave things for you to read.** A kind can have a `file` field holding an HTML or PDF file (up to 10 MB), such as a research report or an English script. Tapping such an item opens a reading screen in the app; an HTML page cut into `.slide` blocks is shown one slide at a time with previous/next buttons. Stored HTML runs in a sandbox with no origin, so its scripts work but cannot read your session cookie or call the API. What a page keeps in `localStorage` (answers in a workbook, ticked boxes) is saved on the server for that item when you use the page in the app's reading screen, and is there again the next time you open it.
- **Every change can be undone.** Each write, by you or an agent, goes to a change log with the row before and after.

## Screens

| Screen | What it is for |
|---|---|
| Today | The capture box and the lines written today (or any earlier day) with their state: new, sorted, or waiting for you. Items due within three days, open tasks, and up to six items with a file: pinned first, then recently opened, then newest. |
| Lists | One list per kind, with an editor generated from the kind's fields. |
| Read | Opens an item's file: a back link, the title, reload, pin, memo, details and "open in a new tab", and the file below. The memo is the item's memo text, shown beside the file (below it on a phone) and saved as you type. |
| Review | Kinds and changes to kinds (such as more choices for a field) proposed by agents (approve or reject), lines an agent could not decide, and the change log with undo buttons. |

It is a PWA, so it can be added to a phone's home screen. Light and dark themes follow the system.

## What agents may do

The server decides from how the request is authenticated, not from what the agent says about itself:

| | You (browser session) | Agent (API key or `X-Actor` header) |
|---|---|---|
| Add lines and items, edit items | yes | yes |
| Mark a line sorted or waiting | yes | yes |
| Rewrite or delete a line you wrote | yes | no (403) |
| Add a kind | yes | proposed only, until approved |
| Approve or reject a proposal | yes | yes, when you tell it to (`decide_proposal`) |
| Big card in 一覧 for a kind | yes | yes (`set_main_kind`) |
| Change or delete a kind | yes | no (403) |
| Undo a change | yes | yes, except changes to kinds |

## Tools for agents

`mcp_server.py` exposes 19 tools (`list_unsorted`, `sort_entry`, `mark_entry`, `propose_kind`, `propose_kind_change`, `list_proposals`, `decide_proposal`, `add_file`, `sync_folder`, `list_items`, `update_item`, `get_today`, `recent_changes`, `undo_change`, ...). The same tools also run as a plain command with no extra packages, which is how slash commands and Codex can use them without registering an MCP server. `sync_folder` brings the HTML and PDF files of a local folder (default `~/myLife/reports`) into 資料: new files become items, changed files replace the file of their item, deleted files mark it done, and unchanged files are skipped, so it is safe to run after every report. A page can set its 分類 with `<meta name="zzzmemo-category" content="お金">`, or stay out with `content="skip"`.

```bash
python mcp_server.py list
python mcp_server.py call list_unsorted '{}'
python mcp_server.py call sort_entry '{"entry_id": "...", "kind": "タスク", "title": "Post the parcel", "due": "2026-11-21T10:00:00"}'
```

As an MCP server (`pip install mcp`; works with mcp 1.x and 2.x):

```json
{
  "mcpServers": {
    "zzzmemo": {
      "command": "python",
      "args": ["<path-to-repo>/mcp_server.py"],
      "env": {"ZZZMEMO_BASE_URL": "https://<your-instance>", "ZZZMEMO_API_KEY": "<key>", "ZZZMEMO_ACTOR": "claude"}
    }
  }
}
```

## API

| Endpoint | |
|---|---|
| `GET/POST /api/entries`, `PATCH/DELETE /api/entries/{id}` | Lines. POST stores multi-line text as one entry per line. |
| `GET/POST /api/kinds`, `PATCH/DELETE /api/kinds/{id}` | Kinds and their fields (text, date, choice, number, bool). |
| `GET/POST /api/items`, `GET/PATCH /api/items/{id}` | Items. Field values are checked against the kind. |
| `GET /api/today` | Today's lines, items due soon, open tasks, kinds awaiting approval. |
| `POST /api/files?name=<file name>`, `GET /files/{id}` | Upload an HTML or PDF file as the raw request body (returns its id), and read it. Files are never rewritten; a new version is a new id. |
| `GET /api/log`, `POST /api/log/{id}/undo` | Change log and undo. Undo is refused if the row changed again afterwards. |

## Run it

Python 3.10+.

```bash
git clone https://github.com/sean-from-japan/ZzzMemo.git
cd ZzzMemo
pip install -r requirements.txt
python qcatch.py          # http://localhost:5000, no login on localhost
python -m pytest tests
```

| Environment variable | |
|---|---|
| `ZZZMEMO_USER`, `ZZZMEMO_PASS` | Password login |
| `ZZZMEMO_GOOGLE_EMAIL`, `GOOGLE_CLIENT_SECRET_JSON`, `GOOGLE_REDIRECT_URI` | "Sign in with Google" for that one address. Google is asked for the email only. |
| `ZZZMEMO_API_KEY` | Key for agents (`X-Api-Key`) |
| `HOST`, `PORT` | Bind address. The server refuses to start on a non-loopback address with no login configured (`ZZZMEMO_ALLOW_NO_AUTH=1` overrides). |

`Dockerfile` and `fly.toml` deploy it to Fly.io with the SQLite file on a volume. `migrate_v1.py` copies data from a version 1 database once.

## Stack

FastAPI and SQLite on the server; one HTML page and one JavaScript module, with no framework or build step, on the client.

```
ZzzMemo/
├── qcatch.py            # starts the server
├── core/db.py           # entries, kinds, items, change log
├── core/google_login.py # "Sign in with Google" (email check)
├── web/server.py        # login, page, service worker
├── web/routers/api.py   # REST API and who may do what
├── web/static/          # index.html, app.js, manifest, sw.js
├── mcp_server.py        # tools for agents (MCP server and CLI)
├── migrate_v1.py        # one-time copy of version 1 data
└── tests/
```

## License

MIT
