// 패널 크기 조절 손잡이(결정기록 0016·0033, 디자인 명세 06 3장). 대시보드 상세·제안 목록이 함께 쓴다.
// 폭은 CSS 변수로 적용하고(인라인 style 속성 문자열을 쓰지 않음), 조절이 끝나면 서버 설정에 저장한다.
"use strict";

const SPLIT_STEP = 16;        // ←→ 한 번에 움직이는 폭(px)
const SPLIT_STEP_BIG = 64;    // Shift + ←→
const SPLIT_SAVE_DELAY = 400; // 키보드 조절 후 저장까지 기다리는 시간(ms)
// 폭 프리셋(결정기록 0033): 두 패널 합계 대비 이 패널의 비율
const SPLIT_PRESETS = [["좁게", 0.3], ["보통", 0.4], ["넓게", 0.55]];

// options: { handle, panel, other, cssVar, prefKey, side: "left"|"right", min, max?, maxRatio?, otherMin }
// - panel: 폭을 바꾸는 패널, other: 맞은편 패널(otherMin 아래로 줄지 않게 한다)
// - side: 패널이 손잡이의 어느 쪽에 있는지. "right"면 손잡이를 왼쪽으로 끌 때 패널이 커진다
// - max(px)·maxRatio(두 패널 합계 대비)는 생략할 수 있다. 생략하면 '전체 − otherMin'까지 넓힌다
// 반환값: { applyRatio(r), reset() } — 프리셋·넓게 보기 같은 화면 기능이 쓴다
function initSplitter(options) {
  const { handle, panel, other, cssVar, prefKey, side, min, otherMin } = options;
  const max = Number.isFinite(options.max) ? options.max : Infinity;
  const maxRatio = Number.isFinite(options.maxRatio) ? options.maxRatio : 1;
  const root = document.documentElement;
  let custom = root.style.getPropertyValue(cssVar) !== "";
  let saveTimer = 0;
  let frame = 0;

  const usable = () => handle.offsetParent !== null;  // 서랍 구간에서는 손잡이를 숨긴다
  const width = () => Math.round(panel.getBoundingClientRect().width);
  const total = () => panel.getBoundingClientRect().width + other.getBoundingClientRect().width;
  // 최대 폭: 설정 최대값 · 화면 비율 상한 · '맞은편 패널이 otherMin 아래로 줄지 않는 범위' 중 가장 작은 값
  const maxNow = () => {
    const all = total();
    return Math.max(min, Math.floor(Math.min(max, all * maxRatio, all - otherMin)));
  };
  const clamp = (value) => Math.max(min, Math.min(maxNow(), Math.round(value)));

  function syncAria() {
    handle.setAttribute("aria-valuemin", String(min));
    handle.setAttribute("aria-valuemax", String(maxNow()));
    handle.setAttribute("aria-valuenow", String(width()));
  }
  function apply(value) {
    root.style.setProperty(cssVar, clamp(value) + "px");
    custom = true;
    syncAria();
  }
  function reset() {
    root.style.removeProperty(cssVar);
    custom = false;
    syncAria();
    clearTimeout(saveTimer);
    api("DELETE", "/api/ui-prefs/" + prefKey);
  }
  function save() {
    clearTimeout(saveTimer);
    if (custom) api("PUT", "/api/ui-prefs", { [prefKey]: width() });
  }
  function applyRatio(ratio) {
    apply(total() * ratio);
    save();
  }

  // 끌기: Pointer Events + requestAnimationFrame
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !usable()) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startWidth = width();
    let latest = startX;
    document.body.classList.add("resizing");
    const onMove = (move) => {
      latest = move.clientX;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const delta = latest - startX;
        apply(startWidth + (side === "right" ? -delta : delta));
      });
    };
    const onUp = () => {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onUp);
      document.body.classList.remove("resizing");
      // 마지막 프레임을 반영한 뒤 저장한다
      requestAnimationFrame(() => { if (latest !== startX) save(); });
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
  });

  handle.addEventListener("dblclick", reset);

  // 키보드: ←→ 16px, Shift+←→ 64px, Home 최소, End 최대, Enter 기본 폭, Shift+F10 프리셋 메뉴
  handle.addEventListener("keydown", (event) => {
    const step = event.shiftKey ? SPLIT_STEP_BIG : SPLIT_STEP;
    const grow = side === "right" ? "ArrowLeft" : "ArrowRight";
    const shrink = side === "right" ? "ArrowRight" : "ArrowLeft";
    if (event.key === grow) apply(width() + step);
    else if (event.key === shrink) apply(width() - step);
    else if (event.key === "Home") apply(min);
    else if (event.key === "End") apply(maxNow());
    else if (event.key === "Enter") { event.preventDefault(); event.stopPropagation(); reset(); return; }
    else if (event.key === "F10" && event.shiftKey) { event.preventDefault(); openPresetMenu(); return; }
    else return;
    event.preventDefault();
    event.stopPropagation();  // 화면의 ←→ 단축키(페이지 넘김 등)와 겹치지 않게 한다
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, SPLIT_SAVE_DELAY);
  });

  // 폭 프리셋 메뉴(우클릭·Shift+F10): 좁게 30% / 보통 40% / 넓게 55% / 기본 폭
  let menu = null;
  function closePresetMenu(refocus) {
    if (!menu) return;
    menu.remove();
    menu = null;
    document.removeEventListener("pointerdown", outsideClose, true);
    if (refocus) handle.focus();
  }
  function outsideClose(event) { if (menu && !menu.contains(event.target)) closePresetMenu(false); }
  function openPresetMenu(x, y) {
    if (!usable()) return;
    closePresetMenu(false);
    const pick = (fn) => () => { fn(); closePresetMenu(true); };
    const items = SPLIT_PRESETS.map(([label, ratio]) => el("button", { type: "button", role: "menuitem",
      class: "split-menu-item", onclick: pick(() => applyRatio(ratio)) }, label + " " + Math.round(ratio * 100) + "%"));
    items.push(el("button", { type: "button", role: "menuitem", class: "split-menu-item", onclick: pick(reset) }, "기본 폭"));
    menu = el("div", { class: "split-menu", role: "menu", "aria-label": "패널 폭 프리셋" }, ...items);
    document.body.append(menu);
    const box = handle.getBoundingClientRect();
    const left = Math.min((x ?? box.right) + 4, window.innerWidth - menu.offsetWidth - 8);
    const top = Math.min(y ?? box.top + 40, window.innerHeight - menu.offsetHeight - 8);
    menu.style.setProperty("--menu-x", Math.max(8, left) + "px");
    menu.style.setProperty("--menu-y", Math.max(8, top) + "px");
    menu.addEventListener("keydown", (event) => {
      const list = [...menu.querySelectorAll("button")];
      const at = list.indexOf(document.activeElement);
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closePresetMenu(true); }
      else if (event.key === "ArrowDown") { event.preventDefault(); list[(at + 1) % list.length].focus(); }
      else if (event.key === "ArrowUp") { event.preventDefault(); list[(at - 1 + list.length) % list.length].focus(); }
      else if (event.key === "Tab") closePresetMenu(false);
    });
    document.addEventListener("pointerdown", outsideClose, true);
    items[0].focus();
  }
  handle.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    openPresetMenu(event.clientX, event.clientY);
  });

  // 창을 줄이면 저장된 폭이 새 최대값을 넘지 않게 줄인다(저장 값은 바꾸지 않는다)
  function fitToWindow() {
    if (!usable()) return;
    if (custom && width() > maxNow()) root.style.setProperty(cssVar, maxNow() + "px");
    syncAria();
  }
  window.addEventListener("resize", fitToWindow);
  fitToWindow();
  return { applyRatio, reset, fit: fitToWindow };
}
