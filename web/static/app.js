// ZzzMemo v2 front end. Talks only to /api/entries, /api/kinds, /api/items,
// /api/today and /api/log. Text from the server is always inserted as text,
// never as HTML, because agents write it.

const $ = (id) => document.getElementById(id);
const WEEK = ["日", "月", "火", "水", "木", "金", "土"];

const state = {
  kinds: [],
  today: null,
  day: localDay(new Date()),
  kind: null,
  sheetItem: null,
};

// ── helpers ──────────────────────────────────────────────────────────────────

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function localDay(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function shiftDay(day, n) {
  const d = new Date(day + "T00:00:00");
  d.setDate(d.getDate() + n);
  return localDay(d);
}

function dayLabel(day, withYear = false) {
  const d = new Date(day + "T00:00:00");
  const y = withYear ? `${d.getFullYear()}年` : "";
  return `${y}${d.getMonth() + 1}月${d.getDate()}日（${WEEK[d.getDay()]}）`;
}

function dueInfo(due) {
  if (!due) return null;
  const d = new Date(due);
  const today = localDay(new Date());
  const day = localDay(d);
  const hm = d.getHours() || d.getMinutes() ? ` ${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}` : "";
  let word = `${d.getMonth() + 1}/${d.getDate()}（${WEEK[d.getDay()]}）`;
  if (day === today) word = "今日";
  else if (day === shiftDay(today, 1)) word = "明日";
  const cls = day < today ? "over" : day <= shiftDay(today, 1) ? "soon" : "";
  return { text: word + hm, cls };
}

let toastTimer;
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.style.display = "block";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.style.display = "none"), 3000);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401) {
    location.href = "/login";
    throw new Error("unauthorized");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = typeof data.detail === "string" ? data.detail : `エラー（${res.status}）`;
    toast(msg);
    throw new Error(msg);
  }
  return data;
}

const kindById = (id) => state.kinds.find((k) => k.id === id);
const activeKinds = () => state.kinds.filter((k) => k.status === "active");

// ── tabs ─────────────────────────────────────────────────────────────────────

function currentTab() {
  const t = location.hash.slice(1);
  return ["today", "list", "review"].includes(t) ? t : "today";
}

async function showTab() {
  const tab = currentTab();
  document.querySelectorAll("section.view").forEach((s) => s.classList.toggle("on", s.id === tab));
  document.querySelectorAll(".seg a").forEach((a) => a.classList.toggle("on", a.dataset.tab === tab));
  if (tab === "today") await renderToday();
  if (tab === "list") await renderList();
  if (tab === "review") await renderReview();
}

async function refreshBadge() {
  const [kinds, entries] = await Promise.all([
    api("/api/kinds?status=proposed"),
    api("/api/entries?state=review"),
  ]);
  const n = kinds.kinds.length + entries.entries.length;
  $("review-badge").textContent = n ? String(n) : "";
}

async function loadKinds() {
  state.kinds = (await api("/api/kinds")).kinds;
}

// ── item row (shared by today and list) ──────────────────────────────────────

function itemRow(item, { showKind = false } = {}) {
  const kind = kindById(item.kind_id);
  const due = dueInfo(item.due);
  const done = item.status === "done";
  const extras = Object.entries(item.fields || {})
    .filter(([, v]) => v !== null && v !== "" && v !== false)
    .map(([k, v]) => h("span", { class: "tag plain" }, v === true ? k : `${k}：${v}`));
  return h("li", { class: "row" + (done ? " is-done" : "") },
    h("input", {
      type: "checkbox", class: "check", checked: done, "aria-label": "完了",
      onchange: async (e) => {
        await api(`/api/items/${item.id}`, { method: "PATCH", body: { status: e.target.checked ? "done" : "open" } });
        toast(e.target.checked ? "完了にしました" : "未完了に戻しました");
        showTab();
      },
    }),
    h("div", { class: "main open", onclick: () => openSheet(item) },
      h("span", { class: "title" }, item.title),
      h("div", { class: "sub" },
        showKind && kind ? h("span", { class: "tag" }, kind.name) : null,
        due ? h("span", { class: `due ${due.cls}` }, due.text) : null,
        extras,
      ),
    ),
  );
}

function fill(listEl, rows, emptyText) {
  listEl.replaceChildren(...(rows.length ? rows : [h("li", { class: "empty" }, emptyText)]));
}

// ── today ────────────────────────────────────────────────────────────────────

function entryRow(e) {
  const li = h("li", { class: "row" });
  const stateTag = { done: h("span", { class: "tag ok" }, "仕分け済み"), review: h("span", { class: "tag warn" }, "判断待ち") }[e.state];
  const time = e.created_at ? e.created_at.slice(11, 16) : "";
  const menu = h("div", { class: "menu", hidden: true },
    h("button", { class: "btn small", type: "button", onclick: () => editEntry(li, e) }, "書き直す"),
    e.state !== "done"
      ? h("button", { class: "btn small", type: "button", onclick: () => setEntryState(e, "done") }, "仕分け済みにする")
      : h("button", { class: "btn small", type: "button", onclick: () => setEntryState(e, "new") }, "未処理に戻す"),
    h("button", { class: "btn small danger", type: "button", onclick: () => deleteEntry(e) }, "削除"),
  );
  li.append(
    h("span", { class: `dot ${e.state === "new" ? "" : e.state}`, title: e.state }),
    h("div", { class: "main" },
      h("span", { class: "title" }, e.body),
      h("div", { class: "sub" }, time, stateTag),
      menu,
    ),
    h("button", { class: "more", type: "button", "aria-label": "操作", onclick: () => (menu.hidden = !menu.hidden) }, "⋯"),
  );
  return li;
}

function editEntry(li, e) {
  const input = h("input", { value: e.body, "aria-label": "書き直す" });
  const save = async () => {
    if (input.value.trim() && input.value !== e.body) {
      await api(`/api/entries/${e.id}`, { method: "PATCH", body: { body: input.value } });
    }
    renderToday();
  };
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.isComposing) save();
    if (ev.key === "Escape") renderToday();
  });
  li.replaceChildren(h("div", { class: "edit-line" }, input,
    h("button", { class: "btn small primary", type: "button", onclick: save }, "保存")));
  input.focus();
}

async function setEntryState(e, st) {
  await api(`/api/entries/${e.id}`, { method: "PATCH", body: { state: st } });
  showTab();
  refreshBadge();
}

async function deleteEntry(e) {
  await api(`/api/entries/${e.id}`, { method: "DELETE" });
  toast("削除しました。確認画面の「最近の変更」から戻せます");
  showTab();
}

async function renderToday() {
  const todayStr = localDay(new Date());
  const now = new Date();
  $("hero-date").textContent = `${now.getFullYear()}年 ${dayLabel(todayStr)}`;
  $("day-label").textContent = state.day === todayStr ? "今日のメモ" : dayLabel(state.day, state.day.slice(0, 4) !== todayStr.slice(0, 4));
  $("day-next").disabled = state.day >= todayStr;
  $("day-today").hidden = state.day === todayStr;

  const [today, entries] = await Promise.all([
    api("/api/today"),
    state.day === todayStr ? null : api(`/api/entries?day=${state.day}`),
  ]);
  state.today = today;
  const list = entries ? entries.entries : today.entries;
  fill($("entries"), list.map(entryRow), state.day === todayStr ? "まだ何も書いていません" : "この日のメモはありません");
  $("entries-count").textContent = list.length ? `${list.length}件` : "";

  const dueIds = new Set(today.due_soon.map((i) => i.id));
  const tasks = today.open_tasks.filter((i) => !dueIds.has(i.id));
  fill($("due-soon"), today.due_soon.map((i) => itemRow(i, { showKind: true })), "3日以内の期日はありません");
  fill($("open-tasks"), tasks.map((i) => itemRow(i)), "未完了のタスクはありません");
  $("due-count").textContent = today.due_soon.length ? `${today.due_soon.length}件` : "";
  $("tasks-count").textContent = tasks.length ? `${tasks.length}件` : "";
}

function autoGrow(ta) {
  ta.style.height = "auto";
  ta.style.height = ta.scrollHeight + "px";
}

async function submitCapture(ev) {
  ev?.preventDefault();
  const ta = $("capture-text");
  const text = ta.value.trim();
  if (!text) return;
  const r = await api("/api/entries", { method: "POST", body: { body: text, day: state.day } });
  ta.value = "";
  autoGrow(ta);
  toast(r.entries.length > 1 ? `${r.entries.length}行を追加しました` : "追加しました");
  renderToday();
}

// ── list ─────────────────────────────────────────────────────────────────────

function restoreKind() {
  let saved = null;
  try { saved = localStorage.getItem("zzz.kind"); } catch {}
  const kinds = activeKinds();
  state.kind = kinds.find((k) => k.id === (state.kind || saved))?.id || kinds[0]?.id || null;
}

async function renderList() {
  restoreKind();
  const kinds = activeKinds();
  $("kind-tabs").replaceChildren(...kinds.map((k) =>
    h("button", {
      type: "button", class: k.id === state.kind ? "on" : "",
      onclick: () => {
        state.kind = k.id;
        try { localStorage.setItem("zzz.kind", k.id); } catch {}
        renderList();
      },
    }, k.name)));
  const kind = kindById(state.kind);
  $("list-title").textContent = kind ? kind.name : "一覧";
  $("list-sub").textContent = kind && kind.fields.length ? `項目：${kind.fields.map((f) => f.name).join("・")}` : "";
  if (!kind) return fill($("items"), [], "種類がありません");

  const showDone = $("show-done").checked;
  const data = await api(`/api/items?kind=${encodeURIComponent(kind.id)}&status=${showDone ? "all" : "open"}`);
  const items = data.items.filter((i) => i.status !== "trashed");
  fill($("items"), items.map((i) => itemRow(i)), showDone ? "まだ何もありません" : "未完了のものはありません");
  const open = items.filter((i) => i.status === "open").length;
  $("items-count").textContent = open ? `${open}件` : "";
}

async function addItem(ev) {
  ev.preventDefault();
  const input = $("add-item-title");
  const title = input.value.trim();
  if (!title || !state.kind) return;
  await api("/api/items", { method: "POST", body: { kind: state.kind, title } });
  input.value = "";
  renderList();
}

// ── item sheet ───────────────────────────────────────────────────────────────

function fieldInput(f, value) {
  const name = `f:${f.name}`;
  let input;
  if (f.type === "choice") {
    input = h("select", { name }, h("option", { value: "" }, "（なし）"),
      f.options.map((o) => h("option", { value: o, selected: o === value }, o)));
  } else if (f.type === "bool") {
    input = h("input", { type: "checkbox", name, checked: value === true });
  } else if (f.type === "number") {
    input = h("input", { type: "number", name, step: "any", value: value ?? "" });
  } else if (f.type === "date") {
    input = h("input", { type: "date", name, value: value ?? "" });
  } else {
    input = h("input", { name, value: value ?? "" });
  }
  return h("label", { class: "field" }, h("span", {}, f.name), input);
}

function renderSheetFields(kind, values) {
  $("sheet-fields").replaceChildren(...(kind ? kind.fields.map((f) => fieldInput(f, values[f.name])) : []));
}

function openSheet(item) {
  state.sheetItem = item;
  const form = $("sheet-form");
  const kind = kindById(item.kind_id);
  $("sheet-heading").textContent = kind ? kind.name : "詳細";
  form.title.value = item.title;
  form.body.value = item.body || "";
  form.due.value = item.due ? item.due.slice(0, 16) : "";
  $("sheet-kind").replaceChildren(...activeKinds().map((k) =>
    h("option", { value: k.id, selected: k.id === item.kind_id }, k.name)));
  renderSheetFields(kind, item.fields || {});
  $("sheet-toggle").textContent = item.status === "done" ? "未完了に戻す" : "完了にする";
  $("sheet").returnValue = "";
  $("sheet").showModal();
}

function readSheetFields(kind) {
  const form = $("sheet-form");
  const out = {};
  for (const f of kind.fields) {
    const el = form.elements[`f:${f.name}`];
    if (!el) continue;
    if (f.type === "bool") out[f.name] = el.checked;
    else if (f.type === "number") out[f.name] = el.value === "" ? null : Number(el.value);
    else out[f.name] = el.value === "" ? null : el.value;
  }
  return out;
}

async function closeSheet(action) {
  const item = state.sheetItem;
  const form = $("sheet-form");
  if (!item || action === "cancel" || !action) return;
  if (action === "trash") {
    await api(`/api/items/${item.id}`, { method: "PATCH", body: { status: "trashed" } });
    toast("ゴミ箱へ移しました。確認画面から戻せます");
    return showTab();
  }
  const kind = kindById(form.kind.value);
  const body = {
    title: form.title.value.trim(),
    body: form.body.value,
    due: form.due.value ? form.due.value + ":00" : null,
    fields: readSheetFields(kind),
  };
  if (kind.id !== item.kind_id) body.kind = kind.id;
  if (action === "toggle") body.status = item.status === "done" ? "open" : "done";
  await api(`/api/items/${item.id}`, { method: "PATCH", body });
  toast("保存しました");
  showTab();
}

// ── review ───────────────────────────────────────────────────────────────────

const STATE_WORDS = { open: "未完了", done: "完了", trashed: "ゴミ箱", new: "未処理", review: "判断待ち",
  active: "使用中", proposed: "確認待ち", rejected: "却下" };
const word = (s) => STATE_WORDS[s] || s;

function logSummary(entry) {
  const row = entry.after || entry.before || {};
  const what = { entries: "メモ", items: "項目", kinds: "種類" }[entry.table_name] || entry.table_name;
  const op = { create: "追加", update: "変更", delete: "削除", undo: "取り消し" }[entry.op] || entry.op;
  let detail = row.title || row.body || row.name || "";
  if (entry.op === "update" && entry.before && entry.after) {
    if (entry.before.status !== entry.after.status) detail += `（${word(entry.before.status)} → ${word(entry.after.status)}）`;
    if (entry.before.state !== entry.after.state) detail += `（${word(entry.before.state)} → ${word(entry.after.state)}）`;
  }
  return { label: `${what}を${op}`, detail };
}

async function renderReview() {
  const [proposed, reviewEntries, log] = await Promise.all([
    api("/api/kinds?status=proposed"),
    api("/api/entries?state=review"),
    api("/api/log?limit=30"),
  ]);

  $("proposed").replaceChildren(...(proposed.kinds.length ? proposed.kinds.map((k) =>
    h("div", { class: "card" },
      h("div", { class: "name" }, k.name),
      h("div", { class: "sub", style: "font-size:.8rem;color:var(--muted)" }, `提案：${k.created_by}`),
      h("div", { class: "fields" }, k.fields.length
        ? k.fields.map((f) => h("span", { class: "tag plain" }, `${f.name}（${f.type}${f.options ? "：" + f.options.join("/") : ""}）`))
        : h("span", { class: "tag plain" }, "項目なし")),
      h("div", { class: "actions" },
        h("button", { class: "btn small primary", type: "button", onclick: () => decideKind(k, "active") }, "承認する"),
        h("button", { class: "btn small", type: "button", onclick: () => decideKind(k, "rejected") }, "却下する"),
      ))) : [h("div", { class: "empty" }, "ありません")]));
  $("proposed-count").textContent = proposed.kinds.length ? `${proposed.kinds.length}件` : "";

  fill($("review-entries"), reviewEntries.entries.map(entryRow), "ありません");
  $("review-count").textContent = reviewEntries.entries.length ? `${reviewEntries.entries.length}件` : "";

  fill($("log"), log.log.map((l) => {
    const s = logSummary(l);
    return h("li", { class: "row" },
      h("div", { class: "main" },
        h("span", { class: "title" }, s.detail || s.label),
        h("div", { class: "sub" },
          h("span", { class: l.actor === "human" ? "tag plain" : "tag" }, l.actor === "human" ? "自分" : l.actor),
          s.label, l.at.slice(5, 16).replace("T", " "),
          l.undone ? h("span", { class: "tag plain" }, "取り消し済み") : null),
      ),
      !l.undone && l.op !== "undo"
        ? h("button", { class: "btn small", type: "button", onclick: () => undo(l) }, "取り消す")
        : null,
    );
  }), "まだ変更はありません");
  refreshBadge();
}

async function decideKind(k, status) {
  await api(`/api/kinds/${k.id}`, { method: "PATCH", body: { status } });
  await loadKinds();
  toast(status === "active" ? `「${k.name}」を追加しました` : "却下しました");
  renderReview();
}

async function undo(l) {
  await api(`/api/log/${l.id}/undo`, { method: "POST" });
  await loadKinds();
  toast("取り消しました");
  renderReview();
}

// ── wiring ───────────────────────────────────────────────────────────────────

const ta = $("capture-text");
ta.addEventListener("input", () => autoGrow(ta));
ta.addEventListener("keydown", (e) => {
  // isComposing: Enter that confirms Japanese input must not submit
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault();
    submitCapture();
  }
});
$("capture").addEventListener("submit", submitCapture);
$("day-prev").addEventListener("click", () => { state.day = shiftDay(state.day, -1); renderToday(); });
$("day-next").addEventListener("click", () => { state.day = shiftDay(state.day, 1); renderToday(); });
$("day-today").addEventListener("click", (e) => { e.preventDefault(); state.day = localDay(new Date()); renderToday(); });
$("add-item").addEventListener("submit", addItem);
$("show-done").addEventListener("change", renderList);
$("sheet-kind").addEventListener("change", (e) => renderSheetFields(kindById(e.target.value), {}));
$("sheet").addEventListener("close", () => closeSheet($("sheet").returnValue));
window.addEventListener("hashchange", showTab);

(async () => {
  await loadKinds();
  await showTab();
  refreshBadge();
  if (currentTab() === "today" && matchMedia("(pointer:fine)").matches) ta.focus();
})();
