// 업무 히스토리: 3단계 조회와 출처 배지(SRCH-01~04), 카드 목록·필터(HIST-03).
"use strict";

let historyModule = "";

function badge(stage, text) {
  const cls = stage === "app" ? "app" : stage === "gk" ? "gk" : "ai";
  return el("span", { class: "src " + cls }, text);
}

function renderStages(data, query) {
  const groups = data.stages.map((stage) => {
    let content;
    if (!stage.ran) content = el("div", { class: "empty" }, "실행 안 함");
    else if (!stage.found) content = el("div", { class: "empty" }, stage.notice || "찾지 못함");
    else if (stage.ai_answer) content = el("div", { class: "step aihit" }, stage.ai_answer.text, el("div", { class: "ai-label" }, stage.ai_answer.label));
    else if (stage.app_hits.length) content = el("div", {}, ...stage.app_hits.map((h) => el("div", { class: "step hit" },
      el("div", { class: "t-title" }, h.title), el("div", { class: "t-sub" }, "원인: " + (h.cause || "-")), el("div", {}, "조치: " + h.action))));
    else content = el("div", {}, ...stage.gk_hits.map((h) => el("div", { class: "step hit" },
      el("a", { class: "link", href: h.link, target: "_blank", rel: "noopener noreferrer" }, h.title))));
    return el("div", { class: "result-group" }, el("div", { class: "sec" }, badge(stage.stage, stage.badge)), content);
  });
  const next = { app: "gk", gk: "ai" }[data.resolved];
  fill(document.getElementById("hres"), ...groups,
    data.message ? el("div", { class: "empty" }, data.message) : null,
    next ? el("button", { class: "btn sm", onclick: () => runSearch(query, next) }, "다음 단계 강제 검색") : null);
}

async function runSearch(query, startFrom) {
  if (!query.trim()) return;
  document.getElementById("hres").replaceChildren(el("div", { class: "empty" }, "찾는 중…"));
  const body = { query: query.trim().slice(0, 200), start_from: startFrom || "app" };
  if (historyModule) body.module = historyModule;
  const result = await api("POST", "/api/search", body);
  if (!result.ok) { document.getElementById("hres").replaceChildren(el("div", { class: "empty" }, errorText(result, "검색하지 못했습니다"))); return; }
  renderStages(result.data, query);
}

async function loadCards() {
  const params = new URLSearchParams();
  if (historyModule) params.set("module", historyModule);
  const result = await api("GET", "/api/history?" + params.toString());
  if (!result.ok || !result.data.length) { document.getElementById("hcards").replaceChildren(el("div", { class: "empty" }, "카드가 없습니다.")); return; }
  document.getElementById("hcards").replaceChildren(...result.data.map((c) => el("div", { class: "step" },
    el("div", { class: "t-title" }, c.title, " ", c.module_tag ? el("span", { class: "tag" }, c.module_tag) : null),
    el("div", { class: "t-sub" }, "문제: " + c.problem), el("div", { class: "t-sub" }, "원인: " + (c.cause || "-")),
    el("div", {}, "조치: " + c.action + (c.tcode ? " (" + c.tcode + ")" : "")),
    el("div", { class: "t-sub" }, "공개 범위: " + ({ private: "비공개", team: "팀", company: "전사" }[c.visibility]) + " · " + (c.gk_link ? "G-Knowledge 등록됨" : "G-Knowledge 미등록")))));
}

document.getElementById("hist-form").addEventListener("submit", (event) => {
  event.preventDefault();
  runSearch(document.getElementById("hq").value);
});
// 구분 필터: 사용자가 정한 구분 목록으로 버튼을 만든다(결정기록 0014)
async function renderCategoryFilter() {
  const box = document.getElementById("hist-module");
  const result = await api("GET", "/api/task-categories");
  const names = result.ok ? result.data.categories : [];
  const pick = (value) => () => {
    historyModule = value;
    for (const other of box.querySelectorAll("button")) other.classList.toggle("on", other.dataset.m === value);
    loadCards();
  };
  box.replaceChildren(el("button", { class: "on", dataset: { m: "" }, onclick: pick("") }, "전체"),
    ...names.map((name) => el("button", { dataset: { m: name }, onclick: pick(name) }, name)));
}
renderCategoryFilter();
loadCards();
if (document.getElementById("hq").value) runSearch(document.getElementById("hq").value);
