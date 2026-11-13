// 공통 화면 도우미. 업무 내용·자격증명을 브라우저 저장소에 넣지 않는다.
"use strict";

// 저장된 패널 폭(서버가 <body data-*>로 내려준 숫자)을 CSS 변수로 옮긴다(결정기록 0016)
for (const [key, cssVar] of [["detailW", "--detail-w"], ["trListW", "--tr-list-w"]]) {
  const px = Number(document.body.dataset[key]);
  if (Number.isInteger(px) && px > 0) document.documentElement.style.setProperty(cssVar, px + "px");
}

const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]').content;

// 상태 변경 요청은 항상 JSON + CSRF 헤더로 보낸다.
async function api(method, url, body) {
  const options = { method, headers: { "X-CSRF-Token": CSRF_TOKEN }, credentials: "same-origin" };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  } else if (method !== "GET") {
    options.headers["Content-Type"] = "application/json";
    options.body = "{}";
  }
  const response = await fetch(url, options);
  let data = null;
  try { data = await response.json(); } catch (e) { data = null; }
  return { ok: response.ok, status: response.status, data };
}

// 텍스트 노드만 넣는 요소 생성기(HTML 문자열 해석 없음, XSS 방지).
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "href" && !/^https:\/\//i.test(String(value))) continue;  // https 링크만 허용
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// replaceChildren은 null·false를 "null"·"false" 글자로 바꾸므로 빈 항목을 걸러서 넣는다.
function fill(node, ...children) {
  node.replaceChildren(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

// 글자 일부를 <mark>로 표시한다(텍스트 노드 + mark 요소, HTML 해석 없음). 범위가 틀리면 그대로 둔다.
function markText(text, start, end) {
  const frag = document.createDocumentFragment();
  const s = Math.max(0, Math.min(start, text.length));
  const e = Math.max(s, Math.min(end, text.length));
  if (e <= s) { frag.append(document.createTextNode(text)); return frag; }
  if (s > 0) frag.append(document.createTextNode(text.slice(0, s)));
  frag.append(el("mark", {}, text.slice(s, e)));
  if (e < text.length) frag.append(document.createTextNode(text.slice(e)));
  return frag;
}

// SVG 요소 생성기(아이콘·그래프 공용). 속성 값은 문자열로만 넣는다.
const SVG_NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
  return node;
}

// Lucide 아이콘(로컬 스프라이트). 주소는 서버가 내용 해시를 붙여 meta에 넣는다(예전 캐시 방지).
// 장식용이므로 화면 읽기에서 숨긴다.
const ICON_SPRITE = (document.querySelector('meta[name="icon-sprite"]') || {}).content || "/static/img/icons.svg";
function icon(name) {
  const svg = svgEl("svg", { class: "ic", "aria-hidden": "true", focusable: "false" });
  svg.append(svgEl("use", { href: ICON_SPRITE + "#i-" + name }));
  return svg;
}

// 토스트: 오른쪽 위 카드 + 아이콘 + (행동 버튼) + 하단 진행 막대(디자인 명세 03, 4-5)
// toast("문구")는 그대로 동작한다. options: { kind: "ok"|"warn"|"error", action, onAction, ms }
const TOAST_MS = 2600;
const TOAST_ACTION_MS = 5000;
const TOAST_ICONS = { ok: "check", warn: "alert-triangle", error: "x-circle" };
function toast(message, options) {
  const opts = options || {};
  const box = document.getElementById("toast");
  const ms = opts.ms || (opts.action ? TOAST_ACTION_MS : TOAST_MS);
  const kind = opts.kind || "ok";
  const hide = () => box.classList.remove("show");
  const action = opts.action ? el("button", { type: "button", class: "toast-act", onclick: () => {
    hide();
    if (opts.onAction) opts.onAction();
  } }, opts.action) : null;
  const bar = el("span", { class: "toast-bar" });
  bar.style.animationDuration = ms + "ms";
  fill(box, icon(TOAST_ICONS[kind] || "check"), el("span", { class: "toast-msg" }, message), action, bar);
  box.className = "toast k-" + kind;
  void box.offsetWidth;  // 애니메이션을 처음부터 다시 시작
  box.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(hide, ms);
  // 마우스를 올리면 멈추고, 떼면 잠시 뒤 닫는다(행동 버튼을 누를 시간)
  box.onmouseenter = () => clearTimeout(toast.timer);
  box.onmouseleave = () => { clearTimeout(toast.timer); toast.timer = setTimeout(hide, 1500); };
}

// Ctrl/⌘ + K: 상단 검색 입력으로 이동
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    const input = document.querySelector("#search-form input");
    if (input) { event.preventDefault(); input.focus(); input.select(); }
  }
});

function openDlg(id) { document.getElementById(id).classList.add("show"); }
function closeDlg(id) { document.getElementById(id).classList.remove("show"); }

function errorText(result, fallback) {
  return (result.data && typeof result.data.error === "string") ? result.data.error : fallback;
}

const SITE_LABELS = { "wiki": "위키", "g-knowledge": "G-Knowledge", "ai-ground": "AI-Ground", "groupware": "그룹웨어" };
