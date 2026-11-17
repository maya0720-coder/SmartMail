// 주간보고 HTML 편집 탭(C1, WIKI-02, 결정기록 0041).
// - 왼쪽 코드 입력(고정폭·줄 번호·Tab 들여쓰기, 200KB), 오른쪽 실시간 미리보기(입력 멈춤 500ms 후)
// - 미리보기는 서버 정리·마스킹 결과(POST /api/weekly/clean-html)를 DOMParser로 읽고 허용 노드만
//   el()로 다시 만든다(richNodes, HTML 문자열 직접 대입 없음). 빠진 태그·속성은 경고줄에 요약한다.
// - 입력 원문은 저장하지 않는다(임시 저장·자동 보관은 정리·마스킹 결과만, weekly.js).
"use strict";

const HTML_EDIT = { timer: 0, seq: 0, clean: "", removed: {}, blocked: null };
const HTML_DEBOUNCE_MS = 500;
const HTML_INDENT = "  ";
const HTML_BLOCK = new Set(["h3", "h4", "p", "hr", "ul", "ol", "li", "pre", "blockquote", "div", "table",
  "caption", "colgroup", "col", "thead", "tbody", "tfoot", "tr", "th", "td"]);

const htmlCode = () => document.getElementById("html-code");

function htmlBytes(text) { return new TextEncoder().encode(text).length; }

function renderGutter() {
  const code = htmlCode();
  const count = Math.max(1, code.value.split("\n").length);
  document.getElementById("html-gutter").textContent = Array.from({ length: count }, (_, i) => String(i + 1)).join("\n");
  document.getElementById("html-gutter").scrollTop = code.scrollTop;
  const size = htmlBytes(code.value);
  const box = document.getElementById("html-size");
  box.textContent = Math.ceil(size / 1024) + "KB / 200KB";
  box.classList.toggle("err", size > RICH_MAX_BYTES);
}

function removedText(removed) {
  const parts = Object.entries(removed || {}).map(([name, n]) => name + " " + n);
  return parts.length ? "제거됨: " + parts.join(", ") : "";
}

function showRemoved(removed, blocked) {
  const box = document.getElementById("html-removed");
  const text = [blocked, removedText(removed)].filter(Boolean).join(" · ");
  box.textContent = text;
  box.classList.toggle("hidden", !text);
  box.classList.toggle("err", Boolean(blocked));
}

// 서버 정리 결과(정리·마스킹된 HTML). 실패하면 null(안내는 경고줄)
async function cleanOnServer(html) {
  if (htmlBytes(html) > RICH_MAX_BYTES) { showRemoved({}, "편집 내용이 너무 큽니다(200KB 이하)"); return null; }
  const result = await api("POST", "/api/weekly/clean-html", { html });
  if (!result.ok) { showRemoved({}, errorText(result, "정리하지 못했습니다")); return null; }
  return result.data;
}

async function refreshHtmlPreview() {
  const seq = ++HTML_EDIT.seq;
  const data = await cleanOnServer(htmlCode().value);
  if (seq !== HTML_EDIT.seq || !data) return;  // 그 사이 더 입력했으면 버린다
  HTML_EDIT.clean = data.html;
  HTML_EDIT.removed = data.removed;
  HTML_EDIT.blocked = data.blocked;
  fill(document.getElementById("html-preview"), ...richNodes(data.html));
  showRemoved(data.removed, data.blocked);
}

function scheduleHtmlPreview() {
  clearTimeout(HTML_EDIT.timer);
  HTML_EDIT.timer = setTimeout(refreshHtmlPreview, HTML_DEBOUNCE_MS);
}

// 줄 맞춤: 허용 노드만 블록 단위로 들여 쓴다(서버 정리 결과 기준)
function prettyHtml(html) {
  const lines = [];
  const holder = el("div", {}, ...richNodes(html));
  const walk = (node, depth) => {
    for (const child of node.childNodes) {
      const tag = child.nodeType === Node.ELEMENT_NODE ? child.tagName.toLowerCase() : "";
      if (!HTML_BLOCK.has(tag) || ![...child.children].some((c) => HTML_BLOCK.has(c.tagName.toLowerCase()))) {
        const one = serializeRich(el("div", {}, child.cloneNode(true)));
        if (one.trim()) lines.push(HTML_INDENT.repeat(depth) + one.trim());
        continue;
      }
      const open = serializeRich(el("div", {}, child.cloneNode(false)));
      const close = "</" + tag + ">";
      lines.push(HTML_INDENT.repeat(depth) + open.slice(0, open.length - close.length));
      walk(child, depth + 1);
      lines.push(HTML_INDENT.repeat(depth) + close);
    }
  };
  walk(holder, 0);
  return lines.join("\n");
}

function setHtmlCode(html, pretty) {
  htmlCode().value = pretty ? prettyHtml(html) : html;
  renderGutter();
  scheduleHtmlPreview();
}

async function htmlCleanPaste() {
  const data = await cleanOnServer(htmlCode().value);
  if (!data) return;
  setHtmlCode(data.html, true);
  toast(removedText(data.removed) || "정리할 태그가 없었습니다", { kind: "ok" });
  if (typeof invalidatePreview === "function") invalidatePreview();
}

async function htmlFormat() {
  const data = await cleanOnServer(htmlCode().value);
  if (data) setHtmlCode(data.html, true);
}

function htmlLoadMenu(event) {
  event.stopPropagation();
  if (closeBulkMenu()) return;
  openBulkMenu(event.currentTarget, [
    ["양식 자동 초안(참고 링크 양식)", async () => {
      const link = selectedLink();
      const result = await api("POST", "/api/weekly/draft", { link_id: link ? link.id : null });
      if (!result.ok) { toast(errorText(result, "불러오지 못했습니다"), { kind: "error" }); return; }
      setHtmlCode(result.data.draft_html || listsToHtml(result.data.form.headings, result.data), true);
      invalidatePreview();
    }],
    ["미리보기 편집 내용", () => { setHtmlCode(richHtml(), true); invalidatePreview(); }],
  ]);
}

function initHtmlEditor(onChange) {
  const code = htmlCode();
  code.addEventListener("input", () => { renderGutter(); scheduleHtmlPreview(); onChange(); });
  code.addEventListener("scroll", () => { document.getElementById("html-gutter").scrollTop = code.scrollTop; });
  // Tab은 들여쓰기(Shift+Tab은 기본 동작: 다음 칸으로 이동)
  code.addEventListener("keydown", (event) => {
    if (event.key !== "Tab" || event.shiftKey || event.ctrlKey || event.altKey) return;
    event.preventDefault();
    document.execCommand("insertText", false, HTML_INDENT);
  });
  document.getElementById("html-clean").addEventListener("click", htmlCleanPaste);
  document.getElementById("html-format").addEventListener("click", htmlFormat);
  document.getElementById("html-load").addEventListener("click", htmlLoadMenu);
  renderGutter();
}
