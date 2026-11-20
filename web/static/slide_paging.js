// Inlined into every stored HTML page by GET /files/{id}. A page cut into two or
// more `.slide` blocks is shown one slide at a time, scaled to fit, with
// previous/next buttons and the arrow keys. Pages with their own #prev/#next
// buttons, or without slides, are left alone. Adapted from the class app's
// slide_paging.js (submission-check skill).
(() => {
  const slides = [...document.querySelectorAll(".slide")];
  if (slides.length < 2 || document.getElementById("prev") || document.getElementById("next")) return;

  const style = document.createElement("style");
  style.textContent = `
    html, body { overflow: hidden !important; }
    .slide[hidden] { display: none !important; }
    .slide:not([hidden]) {
      position: fixed !important; left: 50%; top: calc(50% - 23px);
      margin: 0 !important; transform-origin: center;
    }
    #zzz-slide-pager {
      position: fixed; inset: auto 0 0; z-index: 10; height: 46px;
      box-sizing: border-box; display: flex; gap: 14px;
      justify-content: center; align-items: center;
      font: 14px system-ui; font-variant-numeric: tabular-nums;
      color-scheme: light dark; color: CanvasText; background: Canvas;
      border-top: 1px solid color-mix(in srgb, CanvasText 18%, Canvas);
    }
    #zzz-slide-pager button { font: inherit; padding: 3px 10px; cursor: pointer; }
    #zzz-slide-pager button:disabled { cursor: default; }
    #zzz-slide-pager output { min-width: 4em; text-align: center; }
  `;
  document.head.append(style);

  const pager = document.createElement("nav");
  pager.id = "zzz-slide-pager";
  pager.setAttribute("aria-label", "スライドのページ移動");
  pager.innerHTML = '<button type="button">← 前へ</button><output aria-live="polite"></output><button type="button">次へ →</button>';
  document.body.append(pager);
  const [previous, next] = pager.querySelectorAll("button");
  const counter = pager.querySelector("output");
  let index = 0;

  function fit() {
    const slide = slides[index];
    const scale = Math.max(0.01, Math.min((innerWidth - 20) / slide.offsetWidth,
      (innerHeight - 58) / slide.offsetHeight));
    slide.style.transform = `translate(-50%, -50%) scale(${scale})`;
  }

  function show(page) {
    index = Math.min(slides.length - 1, Math.max(0, page));
    slides.forEach((slide, n) => { slide.hidden = n !== index; });
    counter.value = `${index + 1} / ${slides.length}`;
    previous.disabled = index === 0;
    next.disabled = index === slides.length - 1;
    fit();
  }

  previous.addEventListener("click", () => show(index - 1));
  next.addEventListener("click", () => show(index + 1));
  window.addEventListener("resize", fit);
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.metaKey || event.altKey || event.ctrlKey
      || event.target.closest?.("input, select, textarea, [contenteditable]")) return;
    const direction = ["ArrowLeft", "PageUp"].includes(event.key) ? -1
      : ["ArrowRight", "PageDown"].includes(event.key) ? 1 : 0;
    if (!direction) return;
    event.preventDefault();
    show(index + direction);
  });
  // The ZzzMemo reading screen forwards arrow keys pressed outside the frame.
  window.addEventListener("message", (event) => {
    if (event.source !== parent) return;
    if (event.data === "slide-previous") previous.click();
    if (event.data === "slide-next") next.click();
  });
  show(0);
})();
