// 주간보고: 초안 → 편집 → 미리보기 → 승인 후 게시(WIKI-01~06). 미리보기 후 내용을 바꾸면 다시 미리보기해야 한다.
"use strict";

let weekStart = null;
let lastPreview = null;

const lines = (id) => document.getElementById(id).value.split("\n").map((s) => s.trim()).filter(Boolean).slice(0, 50);
const currentDraft = () => ({ week_start: weekStart, done: lines("wk-done"), planned: lines("wk-planned"), issues: lines("wk-issues") });

// 각 열 머리의 항목 수(줄 수)와 ①②③ 단계 표시줄을 갱신한다.
function updateCounts() {
  for (const id of ["wk-done", "wk-planned", "wk-issues"]) {
    document.getElementById(id + "-cnt").textContent = lines(id).length + "건";
  }
}
function setStep(step) {
  for (const item of document.querySelectorAll("#wk-steps li")) {
    const n = Number(item.dataset.step);
    item.classList.toggle("done", n < step);
    item.classList.toggle("now", n === step);
  }
}

function invalidatePreview() {
  lastPreview = null;
  document.getElementById("btn-publish").disabled = true;
  setStep(1);
  updateCounts();
}

async function loadDraft() {
  const result = await api("POST", "/api/weekly/draft");
  if (!result.ok) { toast(errorText(result, "초안을 만들지 못했습니다")); return; }
  weekStart = result.data.week_start;
  document.getElementById("wk-title").textContent = "주간보고 초안 · " + weekStart.slice(0, 10) + "(목) 주차";
  document.getElementById("wk-done").value = result.data.done.join("\n");
  document.getElementById("wk-planned").value = result.data.planned.join("\n");
  document.getElementById("wk-issues").value = result.data.issues.join("\n");
  invalidatePreview();
  document.getElementById("wk-preview").classList.add("hidden");
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
  const result = await api("POST", "/api/weekly/preview", currentDraft());
  if (!result.ok) { toast(errorText(result, "미리보기를 만들지 못했습니다")); return; }
  lastPreview = result.data;
  const box = document.getElementById("wk-preview");
  fill(box, el("div", { class: "t-sub" }, "페이지: " + lastPreview.title + (lastPreview.base_version ? " (현재 버전 " + lastPreview.base_version + ")" : " (새 페이지 또는 위키 미연결)")),
    lastPreview.current_section ? el("div", { class: "notice" }, "지금 바뀔 기존 본인 영역: " + lastPreview.current_section) : null,
    ...renderPreview(lastPreview.section_html));
  box.classList.remove("hidden");
  document.getElementById("btn-publish").disabled = false;
  setStep(3);  // 미리보기 완료 → 승인·게시 단계
}

async function publish() {
  if (!lastPreview) return;
  const mode = document.querySelector('input[name="wiki-mode"]:checked').value;
  const body = { ...currentDraft(), nonce: lastPreview.nonce, mode, approved: true };
  const send = async () => {
    const result = await api("POST", "/api/weekly/publish", body);
    if (!result.ok) {
      if (result.status === 503 && /로그인/.test(errorText(result, ""))) { requireLogin("wiki", send); return; }
      document.getElementById("wiki-err").textContent = errorText(result, "게시하지 못했습니다");
      return;
    }
    closeDlg("dlg-wiki");
    invalidatePreview();  // 승인 번호는 1회용
    toast("위키에 게시했습니다 (버전 " + result.data.version + ")");
    loadHistory();
  };
  await send();
}

async function loadHistory() {
  const result = await api("GET", "/api/weekly/history");
  if (!result.ok || !result.data.length) return;
  document.getElementById("wk-history").replaceChildren(...result.data.map((r) => el("div", { class: "set-row" },
    el("div", {}, el("div", { class: "k" }, r.week_start.slice(0, 10) + " 주차"), el("div", { class: "s" }, "게시 " + r.published_at.slice(0, 16).replace("T", " ") + " · 버전 " + r.version)),
    r.link ? el("a", { class: "link", href: r.link, target: "_blank", rel: "noopener noreferrer" }, "버전 링크") : null)));
}

for (const id of ["wk-done", "wk-planned", "wk-issues"]) document.getElementById(id).addEventListener("input", invalidatePreview);
document.getElementById("btn-redraft").addEventListener("click", loadDraft);
document.getElementById("btn-preview").addEventListener("click", preview);
document.getElementById("btn-publish").addEventListener("click", () => {
  if (!lastPreview) return;
  document.getElementById("wiki-sub").textContent = lastPreview.title + " 페이지의 본인 영역만 갱신합니다. 다른 팀원 내용은 바꾸지 않습니다.";
  document.getElementById("wiki-err").textContent = "";
  openDlg("dlg-wiki");
});
document.getElementById("wiki-cancel").addEventListener("click", () => closeDlg("dlg-wiki"));
document.getElementById("wiki-ok").addEventListener("click", publish);
loadDraft();
loadHistory();
