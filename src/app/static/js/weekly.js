// 주간보고: 참고 링크 양식 초안 → 미리보기 편집 → 승인 → 게시(WIKI-01~06, 결정기록 0030).
// 기본은 위키에 보일 모양을 직접 고치는 편집기(weekly_editor.js), '간단 편집'은 3칸 목록(하위 호환).
// 미리보기 확인 후 내용을 바꾸면 다시 확인해야 한다(서버 1회용 번호 nonce, WIKI-03).
// 위키 API가 안 되거나 슬랙·기타 웹이면 변환본을 클립보드에 넣고 대상 링크를 연다(폴백).
"use strict";

let editMode = "rich";      // rich: 미리보기 편집 / simple: 간단 편집
let autosaveDirty = false;  // 30초 자동 보관 대상(서버 메모리)
const AUTOSAVE_MS = 30000;

let weekStart = null;
let lastPreview = null;
let form = null;        // 초안 응답의 양식(머리글) 정보. 미리보기·게시 때 그대로 되돌려 보낸다
let links = [];         // 등록된 참고 링크

const FIELDS = [["wk-done", "done"], ["wk-planned", "planned"], ["wk-issues", "issues"]];
const lines = (id) => document.getElementById(id).value.split("\n").map((s) => s.trim()).filter(Boolean).slice(0, 50);
const currentDraft = () => {
  const body = editMode === "rich"
    ? { week_start: weekStart, html: richHtml() }
    : { week_start: weekStart, done: lines("wk-done"), planned: lines("wk-planned"), issues: lines("wk-issues") };
  if (form) body.form = form;
  return body;
};
const listsNow = () => ({ done: lines("wk-done"), planned: lines("wk-planned"), issues: lines("wk-issues") });
const selectedLink = () => links.find((l) => String(l.id) === document.getElementById("wk-link").value) || null;
const targetPlatform = () => (selectedLink() ? selectedLink().platform : "wiki");

// 각 열 머리의 항목 수(줄 수)와 ①②③ 단계 표시줄을 갱신한다.
function updateCounts() {
  for (const [id] of FIELDS) document.getElementById(id + "-cnt").textContent = lines(id).length + "건";
}
function setStep(step) {
  for (const item of document.querySelectorAll("#wk-steps li")) {
    const n = Number(item.dataset.step);
    item.classList.toggle("done", n < step);
    item.classList.toggle("now", n === step);
  }
}

function invalidatePreview() {
  autosaveDirty = true;
  lastPreview = null;
  document.getElementById("btn-publish").disabled = true;
  setStep(1);
  updateCounts();
}

function showManual(text) {
  const box = document.getElementById("wk-manual");
  box.value = text;
  box.classList.remove("hidden");
  box.focus();
  box.select();
}

// 사용한 양식(머리글)을 열 제목과 안내 문구에 반영한다.
function applyForm(next) {
  form = next;
  for (const [id, key] of FIELDS) {
    const label = document.querySelector('label[for="' + id + '"]');
    if (label) label.textContent = next.headings[key];
  }
  document.getElementById("wk-form-note").textContent = "사용한 양식: " + next.label;
}

async function loadLinks() {
  const result = await api("GET", "/api/weekly/links");
  if (!result.ok) return;
  links = result.data.links;
  const select = document.getElementById("wk-link");
  const keep = select.value;
  fill(select, el("option", { value: "" }, "기본 양식(위키)"),
    links.map((l) => el("option", { value: String(l.id) }, "[" + l.platform_label + "] " + (l.name || l.url))));
  select.value = links.some((l) => String(l.id) === keep) ? keep : "";
}

async function loadDraft() {
  const link = selectedLink();
  const result = await api("POST", "/api/weekly/draft", { link_id: link ? link.id : null });
  if (!result.ok) { toast(errorText(result, "초안을 만들지 못했습니다")); return; }
  weekStart = result.data.week_start;
  document.getElementById("wk-title").textContent = "주간보고 초안 · " + weekStart.slice(0, 10) + "(목) 주차";
  document.getElementById("wk-page-title").textContent = result.data.page_title || "-";
  document.getElementById("wk-own-heading").textContent = result.data.own_heading || "-";
  setRichHtml(result.data.draft_html || listsToHtml(result.data.form.headings, result.data));
  RICH.dirty = false;
  document.getElementById("wk-done").value = result.data.done.join("\n");
  document.getElementById("wk-planned").value = result.data.planned.join("\n");
  document.getElementById("wk-issues").value = result.data.issues.join("\n");
  applyForm(result.data.form);
  invalidatePreview();
  autosaveDirty = false;
  document.getElementById("wk-preview").classList.add("hidden");
  document.getElementById("wk-manual").classList.add("hidden");
  offerSavedDraft();
}

// 이 주차의 임시 저장·자동 보관본이 있으면 불러올지 묻는다(마스킹된 사본)
async function offerSavedDraft() {
  const saved = await api("GET", "/api/weekly/saved-draft?week_start=" + encodeURIComponent(weekStart));
  if (!saved.ok || !saved.data.html) return;
  const when = saved.data.saved_at.slice(5, 16).replace("T", " ");
  toast((saved.data.stored ? "임시 저장본" : "자동 보관본") + "(" + when + ")이 있습니다", { kind: "warn", ms: 10000, action: "불러오기",
    onAction: () => { setMode("rich"); setRichHtml(saved.data.html); invalidatePreview(); } });
}

// 서버가 이스케이프한 본인 영역 HTML을 DOMParser로 읽어 텍스트 노드로만 다시 그린다(스크립트 실행 없음).
function renderPreview(sectionHtml) {
  const doc = new DOMParser().parseFromString(sectionHtml, "text/html");
  const nodes = [];
  for (const node of doc.body.children) {
    const tag = node.tagName.toLowerCase();
    if (tag === "ul") nodes.push(el("ul", {}, ...Array.from(node.children).map((li) => el("li", {}, li.textContent))));
    else if (["h2", "h3", "p"].includes(tag)) nodes.push(el(tag, {}, node.textContent));
  }
  return nodes;
}

async function preview() {
  let draft;
  try { draft = currentDraft(); } catch (error) { toast(error.message, { kind: "error" }); return; }
  const result = await api("POST", "/api/weekly/preview", draft);
  if (!result.ok) { toast(errorText(result, "미리보기를 만들지 못했습니다")); return; }
  lastPreview = result.data;
  const box = document.getElementById("wk-preview");
  const info = [el("div", { class: "t-sub" }, "페이지: " + lastPreview.title + (lastPreview.base_version ? " (현재 버전 " + lastPreview.base_version + ")" : " (새 페이지 또는 위키 미연결)")),
    lastPreview.current_section ? el("div", { class: "notice" }, "지금 바뀔 기존 본인 영역: " + lastPreview.current_section) : null];
  if (editMode === "rich") {
    // 서버가 정리·마스킹한 결과를 편집 영역에 그대로 보여 준다(본인 머리글 h2는 읽기 전용 칸에)
    const doc = new DOMParser().parseFromString(lastPreview.section_html, "text/html");
    const head = doc.body.querySelector("h2");
    if (head) head.remove();
    setRichHtml(serializeRich(doc.body));
    fill(box, ...info);
  } else {
    fill(box, ...info, ...renderPreview(lastPreview.section_html));
  }
  box.classList.remove("hidden");
  document.getElementById("btn-publish").disabled = false;
  setStep(2);  // 미리보기 확인 완료 → 승인 단계(게시하면 다시 1단계)
}

// 대상 플랫폼 형식으로 바꿔 클립보드에 복사(게시 아님, 승인 불필요)
async function convertAndCopy() {
  const platform = targetPlatform();
  let draft;
  try { draft = currentDraft(); } catch (error) { toast(error.message, { kind: "error" }); return; }
  const result = await api("POST", "/api/weekly/convert", { ...draft, platform });
  if (!result.ok) { toast(errorText(result, "형식을 바꾸지 못했습니다"), { kind: "error" }); return; }
  const ok = await copyText(result.data.text, platform === "wiki" ? result.data.html : null);
  if (ok) toast("클립보드에 복사했습니다(" + { slack: "슬랙", wiki: "위키", web: "일반 텍스트" }[platform] + " 형식)");
  else { showManual(result.data.text); toast("자동 복사에 실패했습니다. 선택된 내용을 Ctrl+C로 복사해 주세요", { kind: "warn" }); }
}

// 게시 결과 처리: API 게시면 안내만, 폴백이면 변환본을 클립보드에 넣고 대상 링크를 새 창으로 연다.
async function handleDelivery(data) {
  if (data.method === "api") { toast("위키에 게시했습니다 (버전 " + data.version + ")"); return; }
  const ok = await copyText(data.text, data.platform === "wiki" ? data.html : null);
  if (!ok) showManual(data.text);
  const message = (ok ? "변환본을 클립보드에 복사했습니다. " : "자동 복사에 실패했습니다. 아래 내용을 직접 복사해 ") +
    "대상 페이지에 붙여넣어 주세요" + (data.reason ? " (" + data.reason + ")" : "");
  toast(message, { kind: ok ? "ok" : "warn", ms: 8000, action: data.open_link ? "대상 페이지 열기" : null, onAction: () => openHttps(data.open_link) });
  openHttps(data.open_link);
}

async function publish() {
  if (!lastPreview) return;
  const link = selectedLink();
  const mode = document.querySelector('input[name="wiki-mode"]:checked').value;
  const body = { ...currentDraft(), nonce: lastPreview.nonce, mode, approved: true, fallback: true, target_link_id: link ? link.id : null };
  const send = async () => {
    const result = await api("POST", "/api/weekly/publish", body);
    if (!result.ok) {
      if (result.status === 503 && /로그인/.test(errorText(result, ""))) { requireLogin("wiki", send); return; }
      document.getElementById("wiki-err").textContent = errorText(result, "게시하지 못했습니다");
      return;
    }
    closeDlg("dlg-wiki");
    invalidatePreview();  // 승인 번호는 1회용
    await handleDelivery(result.data);
    loadHistory();
  };
  await send();
}

const METHOD_LABELS = { api: "자동 게시(API)", clipboard: "클립보드 복사" };

async function loadHistory() {
  const result = await api("GET", "/api/weekly/history");
  if (result.ok && result.data.length) {
    document.getElementById("wk-history").replaceChildren(...result.data.map((r) => el("div", { class: "set-row" },
      el("div", {}, el("div", { class: "k" }, r.week_start.slice(0, 10) + " 주차"), el("div", { class: "s" }, "게시 " + r.published_at.slice(0, 16).replace("T", " ") + " · 버전 " + r.version)),
      r.link ? el("a", { class: "link", href: r.link, target: "_blank", rel: "noopener noreferrer" }, "버전 링크") : null)));
  }
  const cache = await api("GET", "/api/weekly/cache");
  if (!cache.ok || !cache.data.length) return;
  document.getElementById("wk-cache").replaceChildren(...cache.data.map((c) => el("div", { class: "set-row" },
    el("div", {}, el("div", { class: "k" }, c.week_start.slice(0, 10) + " 주차 ", platformBadge(c.platform, c.platform_label), " ", el("span", { class: "pf-method" }, METHOD_LABELS[c.method] || c.method)),
      el("div", { class: "s" }, c.published_at.slice(0, 16).replace("T", " "))),
    el("div", { class: "actions tight" },
      c.link ? el("a", { class: "link", href: c.link, target: "_blank", rel: "noopener noreferrer" }, "대상 열기") : null,
      el("button", { class: "btn sm", type: "button", onclick: async () => {
        const entry = await api("GET", "/api/weekly/cache/" + c.id);
        if (!entry.ok) { toast(errorText(entry, "불러오지 못했습니다"), { kind: "error" }); return; }
        if (await copyText(entry.data.text, null)) toast("캐시된 본문을 복사했습니다");
        else { showManual(entry.data.text); toast("자동 복사에 실패했습니다. 선택된 내용을 Ctrl+C로 복사해 주세요", { kind: "warn" }); }
      } }, "다시 복사")))));
}

// ---- 편집 방식 전환(간단 편집 ↔ 미리보기 편집, 항목 수 보존) ----
function setMode(next) {
  editMode = next;
  const rich = next === "rich";
  document.getElementById("wk-rich").classList.toggle("hidden", !rich);
  document.getElementById("wk-simple").classList.toggle("hidden", rich);
  for (const [id, on] of [["tab-rich", rich], ["tab-simple", !rich]]) {
    const tab = document.getElementById(id);
    tab.classList.toggle("on", on);
    tab.setAttribute("aria-selected", on ? "true" : "false");
  }
}

function switchMode(next) {
  if (next === editMode || !form) return;
  if (next === "simple") {
    const { lists, lost } = htmlToLists(richEditor(), form.headings);
    if (lost && !window.confirm("표·문단 등 " + lost + "개 서식은 간단 편집으로 옮길 수 없어 빠집니다. 바꿀까요?")) return;
    document.getElementById("wk-done").value = lists.done.join("\n");
    document.getElementById("wk-planned").value = lists.planned.join("\n");
    document.getElementById("wk-issues").value = lists.issues.join("\n");
  } else {
    setRichHtml(listsToHtml(form.headings, listsNow()));
  }
  setMode(next);
  invalidatePreview();
}

async function loadTaskPicker() {
  const result = await api("GET", "/api/tasks");
  if (!result.ok) return;
  const items = result.data.filter((t) => !["dismissed", "suggested"].includes(t.status));
  fill(document.getElementById("wk-task-list"), ...items.map((t) => {
    const text = "[" + (t.module_tag || "기타") + "] " + t.title + " (요청: " + (t.requester_label || t.requester_dept || "-") + ")";
    return el("li", {}, el("button", { type: "button", title: text, onmousedown: (event) => event.preventDefault(),
      onclick: () => { setMode("rich"); insertItem(text); } }, text));
  }));
}

async function saveDraftCopy() {
  if (editMode !== "rich" || !weekStart) { toast("미리보기 편집에서 임시 저장할 수 있습니다", { kind: "warn" }); return; }
  let html;
  try { html = richHtml(); } catch (error) { toast(error.message, { kind: "error" }); return; }
  const result = await api("POST", "/api/weekly/save-draft", { week_start: weekStart, html });
  toast(result.ok ? "임시 저장했습니다(마스킹된 사본)" : errorText(result, "저장하지 못했습니다"), { kind: result.ok ? "ok" : "error" });
  if (result.ok) document.getElementById("wk-save-note").textContent = "임시 저장 " + result.data.saved_at.slice(11, 16);
}

// 30초마다 바뀐 내용을 서버 메모리에 보관(주차별 1개). 브라우저 저장소는 쓰지 않는다
setInterval(async () => {
  if (!autosaveDirty || editMode !== "rich" || !weekStart || document.hidden) return;
  let html;
  try { html = richHtml(); } catch (error) { return; }
  const result = await api("POST", "/api/weekly/autosave", { week_start: weekStart, html });
  if (result.ok) { autosaveDirty = false; document.getElementById("wk-save-note").textContent = "자동 보관 " + result.data.saved_at.slice(11, 16); }
}, AUTOSAVE_MS);

initRichEditor(invalidatePreview);
for (const [id] of FIELDS) document.getElementById(id).addEventListener("input", invalidatePreview);
document.getElementById("tab-rich").addEventListener("click", () => switchMode("rich"));
document.getElementById("tab-simple").addEventListener("click", () => switchMode("simple"));
document.getElementById("btn-save").addEventListener("click", saveDraftCopy);
document.getElementById("btn-redraft").addEventListener("click", () => {
  const edited = RICH.dirty || FIELDS.some(([id]) => lines(id).length);
  if (!edited || window.confirm("지금 편집한 내용을 자동 초안으로 덮어쓸까요?")) loadDraft();
});
document.getElementById("btn-preview").addEventListener("click", preview);
document.getElementById("btn-copy").addEventListener("click", convertAndCopy);
document.getElementById("wk-link").addEventListener("change", () => {
  const edited = FIELDS.some(([id]) => lines(id).length);
  if (!edited || window.confirm("선택한 양식으로 초안을 다시 만들까요? 편집한 내용은 사라집니다.")) loadDraft();
});
document.getElementById("btn-publish").addEventListener("click", () => {
  if (!lastPreview) return;
  const platform = targetPlatform();
  document.getElementById("wiki-sub").textContent = platform === "wiki"
    ? lastPreview.title + " 페이지의 본인 영역만 갱신합니다. 위키 API를 쓸 수 없으면 변환본을 복사하고 위키 페이지를 엽니다."
    : "슬랙·기타 웹은 API 연동이 없어 변환본을 클립보드에 복사하고 대상 링크를 엽니다.";
  document.getElementById("wiki-mode-box").classList.toggle("hidden", platform !== "wiki");
  document.getElementById("wiki-err").textContent = "";
  openDlg("dlg-wiki");
});
document.getElementById("wiki-cancel").addEventListener("click", () => closeDlg("dlg-wiki"));
document.getElementById("wiki-ok").addEventListener("click", publish);
loadLinks().then(loadDraft);
loadHistory();
loadTaskPicker();
