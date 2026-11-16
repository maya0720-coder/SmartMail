// 주간보고 미리보기 편집기(WIKI-02·03·04, 결정기록 0030).
// 위키에 보일 모양(머리글·목록·표)을 그대로 그린 편집 영역(contenteditable)에서 직접 고친다.
// - 서버 HTML은 DOMParser로 읽고 허용 태그·속성만 el()로 다시 만든다(스크립트 실행·위험 속성 없음).
// - 보낼 때도 허용 태그만 직접 직렬화한다(HTML 문자열 대입 없음). 최종 정리·마스킹은 서버가 다시 한다.
// - 본인 영역 머리글·페이지 제목은 편집 영역 밖에 읽기 전용(회색)으로 둔다.
"use strict";

const RICH_TAGS = new Set(["h3", "h4", "p", "br", "ul", "ol", "li", "strong", "b", "em", "i", "u",
  "table", "thead", "tbody", "tr", "th", "td", "a"]);
const RICH_SPAN_MAX = 20;
const RICH_MAX_BYTES = 200000;

const escapeHtml = (text) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const escapeAttr = (text) => escapeHtml(text).replace(/"/g, "&quot;");

function spanValue(node, name) {
  const raw = node.getAttribute(name);
  const n = Number(raw);
  return raw && Number.isInteger(n) && n >= 1 && n <= RICH_SPAN_MAX ? String(n) : null;
}

// 서버 HTML → 허용 노드만 다시 만든 DOM 조각(h2는 h3으로 낮춘다: 본인 영역 경계 보호)
function richNodes(html) {
  const doc = new DOMParser().parseFromString(html || "", "text/html");
  const copy = (node) => {
    if (node.nodeType === Node.TEXT_NODE) return document.createTextNode(node.textContent);
    if (node.nodeType !== Node.ELEMENT_NODE) return null;
    let tag = node.tagName.toLowerCase();
    if (tag === "h2") tag = "h3";
    const children = Array.from(node.childNodes).map(copy).filter(Boolean);
    if (!RICH_TAGS.has(tag)) return children.length ? children : null;  // 태그만 버리고 글자는 남김
    const attrs = {};
    if (tag === "a" && /^https:\/\//i.test(node.getAttribute("href") || "")) {
      Object.assign(attrs, { href: node.getAttribute("href"), target: "_blank", rel: "noopener noreferrer" });
    }
    if (tag === "td" || tag === "th") {
      for (const name of ["colspan", "rowspan"]) { const v = spanValue(node, name); if (v) attrs[name] = v; }
    }
    return el(tag, attrs, ...children);
  };
  return Array.from(doc.body.childNodes).map(copy).flat().filter(Boolean);
}

// 편집 영역 → 허용 태그만 직렬화(속성은 href·colspan·rowspan만)
function serializeRich(root) {
  const walk = (node) => {
    if (node.nodeType === Node.TEXT_NODE) return escapeHtml(node.textContent);
    if (node.nodeType !== Node.ELEMENT_NODE) return "";
    let tag = node.tagName.toLowerCase();
    if (tag === "div") tag = "p";  // 편집 중 생기는 div 줄은 문단으로
    if (tag === "h1" || tag === "h2") tag = "h3";
    const inner = Array.from(node.childNodes).map(walk).join("");
    if (tag === "br") return "<br />";
    if (!RICH_TAGS.has(tag)) return inner;
    let attrs = "";
    if (tag === "a" && /^https:\/\//i.test(node.getAttribute("href") || "")) attrs = ' href="' + escapeAttr(node.getAttribute("href")) + '"';
    if (tag === "td" || tag === "th") {
      for (const name of ["colspan", "rowspan"]) { const v = spanValue(node, name); if (v) attrs += " " + name + '="' + v + '"'; }
    }
    return "<" + tag + attrs + ">" + inner + "</" + tag + ">";
  };
  return Array.from(root.childNodes).map(walk).join("");
}

const RICH = { editor: null, onChange: null, dirty: false };

function richEditor() { return document.getElementById("wk-editor"); }

function setRichHtml(html) {
  const box = richEditor();
  box.replaceChildren(...richNodes(html));
  if (!box.childNodes.length) box.append(el("p", {}, el("br")));
}

function richHtml() {
  const html = serializeRich(richEditor());
  if (new TextEncoder().encode(html).length > RICH_MAX_BYTES) throw new Error("편집 내용이 너무 큽니다(200KB 이하)");
  return html;
}

// ---- 간단 편집 ↔ 미리보기 편집 변환 ----
function listsToHtml(headings, lists) {
  const part = (heading, items) => "<h3>" + escapeHtml(heading) + "</h3>" +
    (items.length ? "<ul>" + items.map((i) => "<li>" + escapeHtml(i) + "</li>").join("") + "</ul>" : "<p>-</p>");
  return part(headings.done, lists.done) + part(headings.planned, lists.planned) + part(headings.issues, lists.issues);
}

// 머리글 아래 목록 항목만 꺼낸다. 표·문단(빈 '-' 제외)은 옮길 수 없어 개수를 함께 돌려준다
function htmlToLists(root, headings) {
  const out = { done: [], planned: [], issues: [] };
  const byHeading = new Map(Object.entries(headings).map(([key, text]) => [text.trim(), key]));
  let current = null;
  let lost = 0;
  for (const node of root.children) {
    const tag = node.tagName.toLowerCase();
    if (tag === "h3" || tag === "h4") { current = byHeading.get(node.textContent.trim()) || null; if (!current) lost++; continue; }
    if ((tag === "ul" || tag === "ol") && current) {
      for (const li of node.querySelectorAll("li")) { const t = li.textContent.trim(); if (t) out[current].push(t); }
    } else if (!(tag === "p" && ["", "-"].includes(node.textContent.trim()))) lost++;
  }
  return { lists: out, lost };
}

// ---- 표 ----
function selectionCell() {
  const sel = window.getSelection();
  const node = sel && sel.anchorNode;
  const base = node && (node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement);
  const cell = base && base.closest("td, th");
  return cell && richEditor().contains(cell) ? cell : null;
}

function insertTable() {
  const row = (tag) => el("tr", {}, el(tag, {}, "항목"), el(tag, {}, "내용"), el(tag, {}, "비고"));
  const table = el("table", {}, el("thead", {}, row("th")), el("tbody", {}, el("tr", {}, el("td", {}, el("br")), el("td", {}, el("br")), el("td", {}, el("br")))));
  insertBlock(table);
}

function tableOp(op) {
  const cell = selectionCell();
  if (!cell) { toast("표 칸 안에 커서를 두고 눌러 주세요", { kind: "warn" }); return; }
  const tr = cell.parentElement;
  const table = cell.closest("table");
  const index = Array.from(tr.children).indexOf(cell);
  if (op === "row+") tr.after(el("tr", {}, ...Array.from(tr.children).map(() => el("td", {}, el("br")))));
  if (op === "row-" && table.querySelectorAll("tr").length > 1) tr.remove();
  if (op === "col+") for (const r of table.querySelectorAll("tr")) { const ref = r.children[index]; if (ref) ref.after(el(ref.tagName.toLowerCase(), {}, el("br"))); }
  if (op === "col-" && tr.children.length > 1) for (const r of table.querySelectorAll("tr")) { const ref = r.children[index]; if (ref) ref.remove(); }
  richChanged();
}

function insertBlock(node) {
  const box = richEditor();
  const sel = window.getSelection();
  const anchor = sel && sel.anchorNode && box.contains(sel.anchorNode) ? sel.anchorNode : null;
  let block = anchor && (anchor.nodeType === Node.ELEMENT_NODE ? anchor : anchor.parentElement);
  while (block && block.parentElement !== box) block = block.parentElement;
  if (block) block.after(node); else box.append(node);
  richChanged();
}

// 업무 끌어 넣기: 커서가 있는 목록 끝(없으면 맨 끝 목록)에 항목을 더한다
function insertItem(text) {
  const box = richEditor();
  const sel = window.getSelection();
  const anchor = sel && sel.anchorNode && box.contains(sel.anchorNode) ? sel.anchorNode : null;
  const base = anchor && (anchor.nodeType === Node.ELEMENT_NODE ? anchor : anchor.parentElement);
  const list = (base && base.closest("ul, ol")) || Array.from(box.querySelectorAll("ul, ol")).pop();
  if (list) list.append(el("li", {}, text)); else box.append(el("ul", {}, el("li", {}, text)));
  richChanged();
}

function richCommand(command, value) {
  richEditor().focus();
  document.execCommand(command, false, value || null);
  richChanged();
}

function richChanged() {
  RICH.dirty = true;
  if (RICH.onChange) RICH.onChange();
}

function initRichEditor(onChange) {
  RICH.onChange = onChange;
  const box = richEditor();
  box.addEventListener("input", richChanged);
  // 붙여넣기는 글자만(서식·스크립트가 섞인 HTML을 받지 않는다)
  box.addEventListener("paste", (event) => {
    event.preventDefault();
    const text = (event.clipboardData && event.clipboardData.getData("text/plain")) || "";
    document.execCommand("insertText", false, text);
  });
  box.addEventListener("drop", (event) => event.preventDefault());
  const actions = {
    h3: () => richCommand("formatBlock", "h3"), h4: () => richCommand("formatBlock", "h4"),
    p: () => richCommand("formatBlock", "p"), bold: () => richCommand("bold"),
    ul: () => richCommand("insertUnorderedList"), ol: () => richCommand("insertOrderedList"),
    undo: () => richCommand("undo"), redo: () => richCommand("redo"), table: insertTable,
    "row+": () => tableOp("row+"), "row-": () => tableOp("row-"), "col+": () => tableOp("col+"), "col-": () => tableOp("col-"),
  };
  for (const button of document.querySelectorAll("#wk-toolbar [data-cmd]")) {
    button.addEventListener("mousedown", (event) => event.preventDefault());  // 선택 영역 유지
    button.addEventListener("click", () => actions[button.dataset.cmd]());
  }
}
