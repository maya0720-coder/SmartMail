// 설정·연결 > 외부 회사 도메인(결정기록 0027): 조회·수정(사용자 확정)·삭제. 글자는 textContent로만.
"use strict";

const CO_SOURCES = { rule: "매핑", ai: "AI 추정", user: "사용자 확정" };

async function loadCompanies() {
  const box = document.getElementById("co-list");
  if (!box) return;
  const result = await api("GET", "/api/companies");
  if (!result.ok) { box.replaceChildren(el("div", { class: "t-sub" }, errorText(result, "불러오지 못했습니다"))); return; }
  document.getElementById("co-count").textContent = String(result.data.length);
  if (!result.data.length) { box.replaceChildren(el("div", { class: "t-sub" }, "아직 확인한 외부 회사 도메인이 없습니다")); return; }
  box.replaceChildren(el("table", { class: "company-table" },
    el("thead", {}, el("tr", {}, el("th", {}, "도메인"), el("th", {}, "회사명"), el("th", {}, "출처"), el("th", {}, ""))),
    el("tbody", {}, ...result.data.map(companyRow))));
}

function companyRow(item) {
  const input = el("input", { class: "inline-input", value: item.company, maxlength: "50", "aria-label": item.domain + " 회사명" });
  const save = el("button", { type: "button", class: "btn sm primary", onclick: async () => {
    const name = input.value.trim();
    if (!name) return;
    const result = await api("PUT", "/api/companies", { domain: item.domain, company: name });
    toast(result.ok ? "저장했습니다" : errorText(result, "저장하지 못했습니다"), { kind: result.ok ? "ok" : "error" });
    if (result.ok) loadCompanies();
  } }, "확정");
  const remove = el("button", { type: "button", class: "btn sm", onclick: async () => {
    if (!window.confirm(item.domain + " 회사명을 지울까요? 다음 확인 때 다시 찾습니다.")) return;
    const result = await api("DELETE", "/api/companies/" + encodeURIComponent(item.domain));
    toast(result.ok ? "지웠습니다" : errorText(result, "지우지 못했습니다"), { kind: result.ok ? "ok" : "error" });
    loadCompanies();
  } }, "삭제");
  return el("tr", {}, el("td", { class: "mono" }, item.domain), el("td", {}, input),
    el("td", {}, CO_SOURCES[item.source] || item.source), el("td", {}, el("span", { class: "actions tight" }, save, remove)));
}

loadCompanies();
