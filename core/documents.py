"""core/documents.py — pages for files that are not HTML, and written documents.

Uploaded Word files (.docx), Markdown and plain text are shown as HTML pages;
the original file is kept and can be downloaded as it is. A document written
in the app (a "document" field) is Markdown: it is shown as a page, printed to
PDF by the browser, and exported as a Word file.
"""

from __future__ import annotations

import html
import io

import mammoth
from docx import Document
from docx.shared import Pt
from markdown_it import MarkdownIt

# Raw HTML in Markdown is shown as text, so a document cannot carry scripts.
# A line break in the text is a line break on the page, as in a word processor.
_MD = MarkdownIt("commonmark", {"html": False, "breaks": True}).enable(["table", "strikethrough"])

# Paper-like: documents are white whatever the app's colour scheme, as in Word.
_CSS = """
:root{color-scheme:light}
body{margin:0;background:#fff;color:#111;font:16px/1.85 "Hiragino Sans","Hiragino Kaku Gothic ProN","Noto Sans JP","Yu Gothic",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:760px;margin:0 auto;padding:40px 32px 80px;overflow-wrap:anywhere}
@media (max-width:640px){main{padding:24px 18px 60px}}
h1{font-size:1.6em;line-height:1.4;margin:1.2em 0 .6em}h2{font-size:1.3em;margin:1.4em 0 .5em;padding-bottom:.2em;border-bottom:1px solid #e4e4e7}
h3{font-size:1.1em;margin:1.2em 0 .4em}h1:first-child,h2:first-child,h3:first-child{margin-top:0}
p{margin:.6em 0}ul,ol{padding-left:1.6em}li{margin:.2em 0}
table{border-collapse:collapse;margin:1em 0;max-width:100%}th,td{border:1px solid #d4d4d8;padding:6px 10px;vertical-align:top}th{background:#f4f4f5}td>p,th>p{margin:0}
code{font-family:ui-monospace,Menlo,monospace;font-size:.9em;background:#f4f4f5;border-radius:4px;padding:.1em .35em}
pre{background:#f4f4f5;border-radius:8px;padding:12px 14px;overflow-x:auto;white-space:pre-wrap}pre code{background:none;padding:0}
blockquote{margin:1em 0;padding:0 1em;border-left:3px solid #d4d4d8;color:#52525b}
hr{border:0;border-top:1px solid #d4d4d8;margin:2em 0}img{max-width:100%;height:auto}a{color:#1a5fd0}
.text{font-family:inherit;background:none;padding:0;white-space:pre-wrap}
@page{size:A4;margin:20mm}
@media print{main{max-width:none;padding:0}}
"""


def page(title: str, body_html: str, script: str = "") -> bytes:
    return (f'<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
            f"<body><main>{body_html}</main>{script}</body></html>").encode("utf-8")


def markdown_html(text: str) -> str:
    return _MD.render(text)


def decode_text(data: bytes) -> str:
    """UTF-8 (with or without BOM), else Shift_JIS as written by Windows."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp932")


def file_page(data: bytes, ext: str, title: str) -> bytes:
    """A Word, Markdown or plain-text file as an HTML page."""
    if ext == "docx":
        body = mammoth.convert_to_html(io.BytesIO(data)).value  # images come inline as data URIs
    elif ext == "md":
        body = markdown_html(decode_text(data))
    else:
        body = f'<pre class="text">{html.escape(decode_text(data))}</pre>'
    return page(title, body)


# ── Word export ───────────────────────────────────────────────────────────────


def _runs(par, children) -> None:
    bold = italic = strike = False
    for c in children:
        t = c.type
        if t == "strong_open" or t == "strong_close":
            bold = t == "strong_open"
        elif t == "em_open" or t == "em_close":
            italic = t == "em_open"
        elif t == "s_open" or t == "s_close":
            strike = t == "s_open"
        elif t in ("softbreak", "hardbreak"):
            par.add_run().add_break()
        elif t in ("text", "code_inline", "image"):
            r = par.add_run(c.content)
            r.bold, r.italic, r.font.strike = bold, italic, strike
            if t == "code_inline":
                r.font.name = "Courier New"


def docx_bytes(title: str, text: str) -> bytes:
    """The Markdown document as a Word file: headings, paragraphs, lists,
    tables, quotes, code, bold, italic and strike-through."""
    doc = Document()
    doc.core_properties.title = title
    lists: list[list] = []  # one [kind, next number] per open list
    quote = 0
    heading = None
    item_start = False
    table = None  # rows of cells, each cell the inline token, while inside a table
    for tok in _MD.parse(text):
        t = tok.type
        if t == "heading_open":
            heading = min(int(tok.tag[1]), 4)
        elif t in ("bullet_list_open", "ordered_list_open"):
            lists.append(["bullet" if t == "bullet_list_open" else "ordered", int(tok.attrGet("start") or 1)])
        elif t in ("bullet_list_close", "ordered_list_close"):
            lists.pop()
        elif t == "list_item_open":
            item_start = True
        elif t == "blockquote_open":
            quote += 1
        elif t == "blockquote_close":
            quote -= 1
        elif t == "table_open":
            table = []
        elif t == "tr_open":
            table.append([])
        elif t == "inline" and table is not None:
            table[-1].append(tok)
        elif t == "table_close":
            cols = max(len(r) for r in table)
            grid = doc.add_table(rows=len(table), cols=cols)
            grid.style = "Table Grid"
            for y, row in enumerate(table):
                for x, cell in enumerate(row):
                    par = grid.cell(y, x).paragraphs[0]
                    _runs(par, cell.children or [])
                    if y == 0:
                        for r in par.runs:
                            r.bold = True
            table = None
        elif t == "inline":
            if heading:
                par = doc.add_heading(level=heading)
                heading = None
            elif lists and item_start:
                kind, n = lists[-1]
                level = min(len(lists), 3)
                if kind == "bullet":
                    par = doc.add_paragraph(style="List Bullet" + ("" if level == 1 else f" {level}"))
                else:
                    # numbers are written out: Word's own numbering would run on
                    # from one list into the next
                    par = doc.add_paragraph(style="List Paragraph")
                    par.paragraph_format.left_indent = Pt(18 * level)
                    par.add_run(f"{n}. ")
                    lists[-1][1] += 1
            elif quote:
                par = doc.add_paragraph(style="Quote")
            else:
                par = doc.add_paragraph()
            item_start = False
            _runs(par, tok.children or [])
        elif t in ("fence", "code_block"):
            par = doc.add_paragraph()
            for i, line in enumerate(tok.content.rstrip("\n").split("\n")):
                if i:
                    par.add_run().add_break()
                par.add_run(line).font.name = "Courier New"
        elif t == "hr":
            doc.add_paragraph("＊　＊　＊").alignment = 1  # centred
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
