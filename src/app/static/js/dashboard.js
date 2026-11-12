// 대시보드: 성과 지표 카드·그래프, 내 할 일, 선택 업무 상세(원본 메일·유사 이력·AI·지식 등록·완료).
"use strict";

let currentGrain = "month";
let allTasks = [];
let currentFilter = "all";
let selectedId = null;

const METRIC_DEFS = {
  "MET-01": "기간 내 완료 확정 업무 수",
  "MET-02": "기한 내 완료 ÷ 기한이 있는 완료 업무",
  "MET-03": "G-Knowledge 등록 완료 업무 ÷ 완료 업무",
  "MET-04": "앱·G-Knowledge에서 해결 ÷ 유사 업무 검색",
};
const GRAIN_LABEL = { year: "연간", half: "반기", quarter: "분기", month: "월", week: "주(목~수)" };

async function loadMetrics() {
  const result = await api("GET", "/api/metrics?grain=" + encodeURIComponent(currentGrain));
  if (!result.ok) return;
  const data = result.data;
  document.getElementById("mk").replaceChildren(...data.cards.map((card) => {
    const unit = card.metric === "MET-01" ? "건" : "";
    const cur = card.current;
    const deltaText = card.delta === null ? "직전 대비 -" :
      "직전 대비 " + (card.metric === "MET-01" ? (card.delta >= 0 ? "+" : "") + Math.round(card.delta) + "건" : (card.delta >= 0 ? "+" : "") + Math.round(card.delta * 100) + "%p");
    const trendClass = card.trend === "개선" ? "up" : card.trend === "악화" ? "dn" : "";
    return el("div", { class: "kpi" },
      el("div", { class: "l" }, card.name),
      el("div", { class: "v" }, formatMetric(card.metric, cur.value), el("span", { class: "u" }, unit),
        cur.reference_only ? el("span", { class: "ref" }, "참고") : null),
      el("div", { class: "dl" }, deltaText, " ", el("span", { class: trendClass }, card.trend)),
      el("div", { class: "chartbox" }, drawChart(card.metric, data.buckets, data.target_due_rate)),
      el("div", { class: "kdef" }, METRIC_DEFS[card.metric], " · 표본 ", String(cur.sample), "건"));
  }));
  document.getElementById("m-desc").textContent =
    GRAIN_LABEL[currentGrain] + " 기준 · 카드는 최근 완료 기간 vs 직전 기간 · 진행 중 기간은 흐리게 표시 · 기한 준수율 목표 " +
    Math.round(data.target_due_rate * 100) + "%(예시, IE팀 확정 전)";
  const header = el("tr", {}, el("th", {}, "기간"), ...Object.values(data.names).map((n) => el("th", {}, n)));
  const rows = data.buckets.map((b) => el("tr", {}, el("td", {}, b.label + (b.partial ? " (진행 중)" : "")),
    ...Object.keys(data.names).map((m) => el("td", {}, formatMetric(m, b.values[m].value) + " (n=" + b.values[m].sample + ")"))));
  document.getElementById("m-tbl").replaceChildren(el("table", {}, el("thead", {}, header), el("tbody", {}, rows)));
}

function statusBadge(task) {
  const cls = { suggested: "b-prop", waiting: "b-wait", in_progress: "b-prog", done_candidate: "b-cand", done: "b-done" }[task.status] || "b-wait";
  return el("span", { class: "badge " + cls }, task.status_label);
}

function dueText(task) {
  if (!task.due_at) return el("span", {}, "미정");
  const date = task.due_at.slice(5, 10).replace("-", "/");
  const cls = task.due_badge === "overdue" ? "due-crit" : task.due_badge === "soon" ? "due-warn" : "";
  return el("span", { class: cls }, date + (task.due_badge === "overdue" ? " 지연" : ""));
}

function renderTodo() {
  const matches = (t) => currentFilter === "all" ? t.status !== "dismissed"
    : currentFilter === "soon" ? Boolean(t.due_badge) : t.status === currentFilter;
  for (const button of document.querySelectorAll("#filters button")) {
    const key = button.dataset.f;
    const count = key === "all" ? allTasks.filter((t) => t.status !== "dismissed").length
      : allTasks.filter((t) => key === "soon" ? Boolean(t.due_badge) : t.status === key).length;
    button.textContent = button.textContent.replace(/\s\d+$/, "") + " " + count;
  }
  document.getElementById("todo").replaceChildren(...allTasks.filter(matches).map((task) => el("tr",
    { class: "row" + (task.id === selectedId ? " sel" : ""), onclick: () => selectTask(task.id) },
    el("td", {}, el("div", { class: "t-title" }, task.title),
      el("div", { class: "t-sub" }, task.module_tag ? el("span", { class: "tag" }, task.module_tag) : null, task.source === "manual" ? "수동" : "메일")),
    el("td", {}, task.requester_dept || "-"), el("td", {}, dueText(task)), el("td", {}, statusBadge(task)))));
}

async function loadTasks() {
  const result = await api("GET", "/api/tasks");
  if (result.ok) { allTasks = result.data; renderTodo(); }
  const alerts = await api("GET", "/api/alerts");
  const box = document.getElementById("alerts");
  if (alerts.ok && alerts.data.length) {
    box.replaceChildren("삭제 2일 전 · G-Knowledge 미등록 완료 업무 " + alerts.data.length + "건: ",
      ...alerts.data.map((t) => el("button", { class: "btn sm gk", onclick: () => openGkPreview(t.id, refresh) }, t.title + " 등록")));
    box.classList.remove("hidden");
  } else box.classList.add("hidden");
}

async function changeStatus(taskId, status) {
  const result = await api("POST", "/api/tasks/" + taskId + "/status", { status });
  toast(result.ok ? "상태를 바꿨습니다" : errorText(result, "바꾸지 못했습니다"));
  await refresh();
}

function stageView(stage) {
  const cls = stage.stage === "app" ? "app" : stage.stage === "gk" ? "gk" : "ai";
  const head = el("span", { class: "src " + cls }, stage.badge);
  if (!stage.ran) return el("div", { class: "step miss" }, head, "실행 안 함(앞 단계에서 찾음)");
  if (!stage.found) return el("div", { class: "step miss" }, head, stage.notice || "찾지 못함");
  if (stage.ai_answer) {
    return el("div", { class: "step aihit" }, head, stage.ai_answer.text,
      el("div", { class: "ai-label" }, stage.ai_answer.label),
      ...stage.ai_answer.references.map((r) => el("div", {}, el("a", { class: "link", href: r, target: "_blank", rel: "noopener noreferrer" }, r))));
  }
  const items = stage.app_hits.length ? stage.app_hits.map((h) => el("div", {}, h.title + " — " + h.action))
    : stage.gk_hits.map((h) => el("div", {}, el("a", { class: "link", href: h.link, target: "_blank", rel: "noopener noreferrer" }, h.title)));
  return el("div", { class: "step hit" }, head, ...items);
}

async function runTaskSearch(task, startFrom, box) {
  box.replaceChildren(el("div", { class: "empty" }, "찾는 중…"));
  const result = await api("POST", "/api/search", { query: task.title, task_id: task.id, start_from: startFrom });
  if (!result.ok) { box.replaceChildren(el("div", { class: "empty" }, errorText(result, "검색하지 못했습니다"))); return; }
  const data = result.data;
  const next = { app: "gk", gk: "ai" }[data.resolved];
  box.replaceChildren(...data.stages.map(stageView),
    data.message ? el("div", { class: "empty" }, data.message) : null,
    el("div", { class: "actions" },
      next ? el("button", { class: "btn sm", onclick: () => runTaskSearch(task, next, box) }, "다음 단계 검색") : null,
      el("button", { class: "btn sm", onclick: async () => { await api("POST", "/api/search/feedback", { task_id: task.id, helpful: true }); toast("평가를 남겼습니다"); } }, "도움 됨"),
      el("button", { class: "btn sm", onclick: async () => { await api("POST", "/api/search/feedback", { task_id: task.id, helpful: false }); toast("평가를 남겼습니다"); } }, "도움 안 됨")));
}

async function showMail(refId, box) {
  const result = await api("GET", "/api/mail/" + refId);
  if (!result.ok) { box.replaceChildren(errorText(result, "원본을 읽지 못했습니다")); return; }
  const m = result.data;
  if (m.missing) { box.replaceChildren(el("div", { class: "empty" }, m.notice)); return; }
  box.replaceChildren(
    el("div", { class: "h" }, el("span", {}, m.subject), el("span", {}, m.sender_name)),
    el("div", { class: "mail-body" }, m.body),
    m.attachments.length ? el("div", { class: "t-sub" }, "첨부(열지 않음): " + m.attachments.join(", ")) : null,
    m.can_open ? el("button", { class: "btn sm mt8", onclick: async () => {
      const r = await api("POST", "/api/mail/" + refId + "/open"); if (!r.ok) toast(errorText(r, "열지 못했습니다"));
    } }, "아웃룩에서 열기") : null);
}

async function cardEditor(task, box) {
  const draft = await api("POST", "/api/history/" + task.id + "/draft");
  if (!draft.ok) { toast(errorText(draft, "초안을 만들지 못했습니다")); return; }
  const field = (label, value) => el("div", { class: "field" }, el("label", {}, label), el("textarea", { maxlength: "2000" }, value || ""));
  const problem = field("문제", draft.data.problem), cause = field("원인", draft.data.cause), action = field("조치", draft.data.action);
  const tcode = el("input", { class: "inline-input", value: draft.data.tcode || "", maxlength: "100", placeholder: "T-code/프로그램" });
  box.replaceChildren(el("div", { class: "sec" }, "히스토리 카드(회신 메일 기반 초안 · 확인 후 저장)"), problem, cause, action, tcode,
    el("div", { class: "actions" }, el("button", { class: "btn sm primary", onclick: async () => {
      const result = await api("POST", "/api/history/" + task.id, {
        problem: problem.querySelector("textarea").value, cause: cause.querySelector("textarea").value,
        action: action.querySelector("textarea").value, tcode: tcode.value || null, tags: task.module_tag ? [task.module_tag] : [],
      });
      toast(result.ok ? "히스토리 카드를 저장했습니다" : errorText(result, "저장하지 못했습니다"));
    } }, "카드 저장")));
}

async function selectTask(taskId) {
  selectedId = taskId;
  renderTodo();
  const result = await api("GET", "/api/tasks/" + taskId);
  const panel = document.getElementById("detail");
  if (!result.ok) { panel.replaceChildren(el("div", { class: "empty" }, errorText(result, "불러오지 못했습니다"))); return; }
  const task = result.data;
  const mailBox = el("div", { class: "mail" }, el("div", { class: "empty" }, task.mail_refs.length ? "원본은 볼 때마다 메일함에서 다시 읽습니다" : "연결된 메일 없음"));
  const searchBox = el("div", {});
  const cardBox = el("div", {});
  const buttons = [];
  if (task.status === "waiting" || task.status === "on_hold") buttons.push(el("button", { class: "btn sm", onclick: () => changeStatus(task.id, "in_progress") }, "진행 시작"));
  if (["waiting", "in_progress", "done_candidate"].includes(task.status)) {
    buttons.push(el("button", { class: "btn sm primary", onclick: () => changeStatus(task.id, "done") }, "완료 확정"));
    buttons.push(el("button", { class: "btn sm", onclick: () => changeStatus(task.id, "on_hold") }, "보류"));
  }
  if (task.status === "done") {
    buttons.push(el("button", { class: "btn sm", onclick: () => cardEditor(task, cardBox) }, "히스토리 카드"));
    buttons.push(el("button", { class: "btn sm gk", onclick: () => openGkPreview(task.id, refresh) }, task.gk_link ? "G-Knowledge 재등록" : "G-Knowledge 지식 등록"));
  }
  buttons.push(el("button", { class: "btn sm ai", onclick: () => runTaskSearch(task, "ai", searchBox) }, "AI 지식 요청"));
  // replaceChildren은 null을 "null" 글자로 바꾸므로 빈 항목은 걸러서 넘긴다.
  panel.replaceChildren(...[
    el("h3", {}, task.title, statusBadge(task)),
    el("div", { class: "meta" },
      el("div", {}, "요청 부서"), el("div", {}, task.requester_dept || "-"),
      el("div", {}, "기한"), el("div", {}, dueText(task)),
      el("div", {}, "모듈"), el("div", {}, task.module_tag || "-"),
      el("div", {}, "근거"), el("div", {}, task.evidence || "-"),
      el("div", {}, "지식 등록"), el("div", {}, task.gk_link ? el("a", { class: "link", href: task.gk_link, target: "_blank", rel: "noopener noreferrer" }, "등록됨") : "미등록")),
    el("div", { class: "sec" }, "원본 메일 참조"),
    ...task.mail_refs.map((ref) => el("button", { class: "btn sm", onclick: () => showMail(ref.id, mailBox) }, "원본 보기 · " + (ref.received_at || "").slice(5, 16).replace("T", " "))),
    mailBox,
    el("div", { class: "sec" }, "유사 이력 (앱 → G-Knowledge → AI)"),
    el("button", { class: "btn sm", onclick: () => runTaskSearch(task, "app", searchBox) }, "유사 이력 찾기"),
    searchBox,
    el("div", { class: "actions" }, ...buttons),
    cardBox,
    task.retention_days_left != null ? el("div", { class: "ret" }, "보관 D-" + task.retention_days_left + " · 보관 기간이 지나면 앱에서 삭제됩니다" + (task.gk_link ? "" : " (G-Knowledge 미등록)")) : null,
  ].filter(Boolean));
}

async function refresh() {
  await Promise.all([loadMetrics(), loadTasks()]);
  if (selectedId !== null) await selectTask(selectedId);
}

for (const button of document.querySelectorAll("#seg button")) {
  button.addEventListener("click", () => {
    for (const other of document.querySelectorAll("#seg button")) other.classList.toggle("on", other === button);
    currentGrain = button.dataset.g;
    loadMetrics();
  });
}
for (const button of document.querySelectorAll("#filters button")) {
  button.addEventListener("click", () => {
    for (const other of document.querySelectorAll("#filters button")) other.classList.toggle("on", other === button);
    currentFilter = button.dataset.f;
    renderTodo();
  });
}
document.getElementById("toggle-table").addEventListener("click", (event) => {
  event.preventDefault();
  document.getElementById("m-tbl").classList.toggle("hidden");
});
// 백그라운드 자동 수집이 끝나면 화면을 새로 고친다(처음 30일 수집은 몇 분 걸릴 수 있음).
const SYNC_POLL_MS = 15000;
let lastSyncSeen;
async function loadSyncStatus() {
  const result = await api("GET", "/api/mail/sync-status");
  const target = document.getElementById("sync-status");
  if (!result.ok) { target.textContent = "메일 자동 수집 상태를 읽지 못했습니다"; return; }
  const data = result.data;
  const every = data.interval_minutes > 0 ? data.interval_minutes + "분마다 자동 새로고침" : "자동 새로고침 꺼짐";
  if (data.syncing) {
    target.textContent = data.last_synced_at ? "메일 수집 중…" : "최근 " + data.initial_days + "일 메일을 처음 수집하는 중… (몇 분 걸릴 수 있습니다)";
  } else {
    target.textContent = data.last_synced_at
      ? "마지막 메일 수집 " + data.last_synced_at.slice(5, 16).replace("T", " ") + " (" + every + ")"
      : "처음 실행 시 최근 " + data.initial_days + "일 메일을 자동 수집합니다";
  }
  if (lastSyncSeen !== undefined && data.last_synced_at !== lastSyncSeen) refresh();
  lastSyncSeen = data.last_synced_at;
}
setInterval(loadSyncStatus, SYNC_POLL_MS);
async function collectMail(body) {
  const result = await api("POST", "/api/mail/collect", body);
  if (!result.ok) { toast(errorText(result, "수집하지 못했습니다")); return; }
  toast("새 메일 " + result.data.new_messages + "건 · 제안 " + result.data.suggested + "건 · 제외 " + result.data.excluded_messages + "건");
  await loadSyncStatus();
  await refresh();
}
document.getElementById("btn-collect").addEventListener("click", () => collectMail({ period: "sync" }));
document.getElementById("btn-range-toggle").addEventListener("click", () => {
  document.getElementById("range-form").classList.toggle("hidden");
});
document.getElementById("range-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const start = document.getElementById("range-start").value;
  const end = document.getElementById("range-end").value;
  if (!start || !end) { toast("시작일과 종료일을 고르세요"); return; }
  collectMail({ period: "range", start: start, end: end });
});
loadSyncStatus();
document.getElementById("btn-manual").addEventListener("click", async () => {
  const title = window.prompt("업무 제목을 입력하세요 (200자 이내)");
  if (!title) return;
  const result = await api("POST", "/api/tasks", { title: title.slice(0, 200) });
  toast(result.ok ? "업무를 추가했습니다" : errorText(result, "추가하지 못했습니다"));
  await refresh();
});
refresh();
