// 설정·연결 화면: 사이트별 로그인 상태, 로그아웃, 전체 삭제.
"use strict";

function renderSiteRows(items) {
  const box = document.getElementById("setconn");
  box.replaceChildren(...items.map((item) => {
    const label = SITE_LABELS[item.site] || item.site;
    const action = item.logged_in
      ? el("button", { class: "btn sm", onclick: async () => {
          await api("POST", "/api/logout", { site: item.site });
          toast(label + " 로그아웃됨"); renderConnections();
        } }, "로그아웃")
      : el("button", { class: "btn sm primary", onclick: () => requireLogin(item.site) }, "로그인");
    return el("div", { class: "set-row" },
      el("div", {}, el("div", { class: "k" }, item.url
        ? el("a", { class: "site-link", href: item.url, target: "_blank", rel: "noopener noreferrer", title: label + " 사이트 열기" }, label, icon("chevron-right"))
        : label),
        el("div", { class: "s" }, item.logged_in ? "로그인됨 (세션은 메모리에만 유지)" : "로그인 안 됨",
          item.browser_login ? " · 사이트 로그인 화면 사용(2단계 인증·SSO)" : "")),
      action);
  }));
}

document.addEventListener("connections", (event) => renderSiteRows(event.detail));

document.getElementById("btn-logout-all").addEventListener("click", async () => {
  const result = await api("POST", "/api/logout-all");
  toast(result.ok ? "모든 사이트에서 로그아웃했습니다" : errorText(result, "처리하지 못했습니다"));
  renderConnections();
});

document.getElementById("btn-wipe").addEventListener("click", async () => {
  if (!window.confirm("로컬 데이터를 모두 지울까요? 되돌릴 수 없습니다.")) return;
  const result = await api("POST", "/api/data/wipe", { confirm: true });
  toast(result.ok ? "로컬 데이터를 모두 지웠습니다" : errorText(result, "삭제하지 못했습니다"));
  loadRuleCount();
});

// 화면 배치(패널 폭) 초기화(결정기록 0016)
document.getElementById("btn-ui-reset").addEventListener("click", async () => {
  const result = await api("DELETE", "/api/ui-prefs");
  if (result.ok) for (const cssVar of ["--detail-w", "--tr-list-w"]) document.documentElement.style.removeProperty(cssVar);
  toast(result.ok ? "화면 배치를 기본값으로 되돌렸습니다" : errorText(result, "초기화하지 못했습니다"), { kind: result.ok ? "ok" : "error" });
});

// '업무 아님' 학습 규칙 수·초기화(결정기록 0015)
async function loadRuleCount() {
  const result = await api("GET", "/api/triage-rules");
  document.getElementById("rules-count").textContent = result.ok ? String(result.data.rules) : "-";
}
document.getElementById("btn-rules-reset").addEventListener("click", async () => {
  if (!window.confirm("'업무 아님' 학습을 초기화할까요? 되돌릴 수 없습니다.")) return;
  const result = await api("DELETE", "/api/triage-rules", { confirm: true });
  toast(result.ok ? "학습 규칙 " + result.data.deleted + "개를 지웠습니다" : errorText(result, "초기화하지 못했습니다"),
    { kind: result.ok ? "ok" : "error" });
  loadRuleCount();
});
loadRuleCount();

function showTitleRule(data) {
  document.getElementById("title-format").value = data.page_title_format;
  const select = document.getElementById("title-offset");
  select.replaceChildren(...data.weekday_labels.map((label, index) =>
    el("option", { value: String(index) }, label + (index === 0 ? " (주 시작일)" : ""))));
  select.value = String(data.offset_days);
  document.getElementById("title-example").textContent = data.example;
}
async function loadTitleRule() {
  const result = await api("GET", "/api/weekly/title-rule");
  if (result.ok) showTitleRule(result.data);
}
document.getElementById("title-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const result = await api("PUT", "/api/weekly/title-rule", {
    page_title_format: document.getElementById("title-format").value,
    offset_days: Number(document.getElementById("title-offset").value),
  });
  if (!result.ok) { toast(errorText(result, "저장하지 못했습니다")); return; }
  showTitleRule(result.data);
  toast("제목 규칙을 저장했습니다");
});
loadTitleRule();

// 업무 구분 목록(사용자 정의, 결정기록 0014)
async function loadCategories() {
  const result = await api("GET", "/api/task-categories");
  if (!result.ok) return;
  document.getElementById("cat-input").value = result.data.categories.join(", ");
  document.getElementById("cat-approval").textContent = result.data.approval_category;
}
document.getElementById("cat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const names = document.getElementById("cat-input").value.split(",").map((s) => s.trim()).filter(Boolean);
  const result = await api("PUT", "/api/task-categories", { categories: names });
  if (!result.ok) { toast(errorText(result, "저장하지 못했습니다"), { kind: "error" }); return; }
  document.getElementById("cat-input").value = result.data.categories.join(", ");
  toast("업무 구분을 저장했습니다" + (result.data.recategorized ? " · " + result.data.recategorized + "건 다시 구분" : ""));
});
loadCategories();

document.getElementById("btn-detect").addEventListener("click", async () => {
  const result = await api("POST", "/api/mail/detect");
  if (result.ok) document.getElementById("collect-desc").textContent = result.data.message;
  else toast(errorText(result, "확인하지 못했습니다"));
});
