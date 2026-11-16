// 대시보드(디자인 명세 03): 압축 KPI·상태 도넛, 내 할 일(체크·정렬·페이지·검색 거르기·키보드), 일괄 작업.
// 상세 패널·서랍은 dashboard_detail.js, 상태 변경·일괄 처리는 todo_actions.js, 목록 계산은 todo_list.js.
"use strict";

let currentGrain = "month";
let allTasks = [];
// 기본 필터: 전체(무시만 제외). 사용자 요청으로 기본값을 '전체'로 둔다(결정기록 0018).
// 제안은 '제안된 업무' 화면에서도 정리할 수 있고, 상단 배너로 안내한다(점검 D3).
let currentFilter = "all";
let selectedId = null;
let searchTerm = "";
const checked = new Set();  // 일괄 작업 선택(메모리에만)

const METRIC_DEFS = {
  "MET-01": "기간 내 완료 확정 업무 수",
  "MET-02": "기한 내 완료 ÷ 기한이 있는 완료 업무",
  "MET-03": "G-Knowledge 등록 완료 업무 ÷ 완료 업무",
  "MET-04": "앱·G-Knowledge에서 해결 ÷ 유사 업무 검색",
};
const METRIC_ICONS = { "MET-01": "check", "MET-02": "target", "MET-03": "book-open", "MET-04": "repeat" };
const GRAIN_LABEL = { year: "연간", half: "반기", quarter: "분기", month: "월", week: "주(목~수)" };

// ---- 성과 지표(압축 KPI: 이름 / 값 + 작은 그래프 / 직전 대비) ----
async function loadMetrics() {
  const result = await api("GET", "/api/metrics?grain=" + encodeURIComponent(currentGrain));
  if (!result.ok) return;
  const data = result.data;
  const range = data.buckets.length ? data.buckets[0].label + " ~ " + data.buckets[data.buckets.length - 1].label : "";
  document.getElementById("mk").replaceChildren(...data.cards.map((card) => {
    const cur = card.current;
    const definition = METRIC_DEFS[card.metric];
    const deltaText = card.delta === null ? "직전 대비 -" :
      "직전 대비 " + (card.metric === "MET-01" ? (card.delta >= 0 ? "+" : "") + Math.round(card.delta) + "건" : (card.delta >= 0 ? "+" : "") + Math.round(card.delta * 100) + "%p");
    const trendClass = card.trend === "개선" ? "up" : card.trend === "악화" ? "dn" : "";
    return el("div", { class: "kpi", title: definition + " · 표본 " + cur.sample + "건 · " + range },
      el("div", { class: "l" }, icon(METRIC_ICONS[card.metric] || "target"), el("span", {}, card.name),
        cur.reference_only ? el("span", { class: "ref" }, "참고") : null),
      el("div", { class: "vrow" },
        el("div", { class: "v" }, formatMetric(card.metric, cur.value), el("span", { class: "u" }, card.metric === "MET-01" ? "건" : "")),
        el("div", { class: "chartbox" }, drawChart(card.metric, data.buckets, data.target_due_rate, definition))),
      el("div", { class: "dl" }, deltaText, card.trend ? " · " : "", el("span", { class: trendClass }, card.trend || "")));
  }));
  const desc = GRAIN_LABEL[currentGrain] + " 기준 · 카드는 최근 완료 기간 vs 직전 기간 · 진행 중 기간은 흐리게 표시 · 기한 준수율 목표 " +
    Math.round(data.target_due_rate * 100) + "%(예시, IE팀 확정 전)";
  document.getElementById("m-desc").textContent = desc;
  document.getElementById("toggle-table").title = desc;
  const header = el("tr", {}, el("th", {}, "기간"), ...Object.values(data.names).map((n) => el("th", {}, n)));
  const rows = data.buckets.map((b) => el("tr", {}, el("td", {}, b.label + (b.partial ? " (진행 중)" : "")),
    ...Object.keys(data.names).map((m) => el("td", {}, formatMetric(m, b.values[m].value) + " (n=" + b.values[m].sample + ")"))));
  document.getElementById("m-tbl").replaceChildren(el("table", {}, el("thead", {}, header), el("tbody", {}, rows)));
}

// ---- 내 할 일 ----
// 기본 정렬: 스레드 마지막 메일이 최근인 순(결정기록 0028)
let todoSort = { key: "recent", dir: -1 };
let todoPage = 1;
let perPage = 10;
let currentView = { items: [], page: 1, pages: 1, total: 0 };

function filteredTasks() {
  const term = searchTerm.trim().toLowerCase();
  const byStatus = (t) => currentFilter === "all" ? t.status !== "dismissed"
    : currentFilter === "todo" ? t.status !== "dismissed" && t.status !== "suggested"
    : currentFilter === "soon" ? Boolean(t.due_badge)
    : currentFilter === "followup" ? isFollowup(t) : t.status === currentFilter;
  const byTerm = (t) => !term || [t.title, t.requester_label, t.requester_dept, t.module_tag].some((v) => v && v.toLowerCase().includes(term));
  return sortTasks(allTasks.filter((t) => byStatus(t) && byTerm(t)), todoSort);
}

function todoRow(task) {
  const box = el("input", { type: "checkbox", class: "cb", "aria-label": task.title + " 선택", checked: checked.has(task.id),
    onclick: (event) => event.stopPropagation(),
    onchange: (event) => { if (event.target.checked) checked.add(task.id); else checked.delete(task.id); renderBulk(); renderHeadCheck(); } });
  const sel = task.id === selectedId;
  return el("tr", { class: "row" + (sel ? " sel" : ""), tabindex: "-1", "aria-selected": sel ? "true" : "false", dataset: { id: String(task.id) },
    onclick: (event) => openTask(task.id, event.currentTarget) },
    el("td", { class: "c-cb" }, box),
    el("td", { class: "c-title", title: task.title }, el("span", { class: "t-title" }, task.title),
      // 요약 상태는 'AI 요약'이 아닐 때만 작게 표시(결정기록 0029)
      task.summary_state && task.summary_state !== "done"
        ? summaryChip(task.summary_state)
        : el("span", { class: "t-sub" }, task.source === "manual" ? "수동" : "메일"),
      // 목록이 좁아져 열을 숨길 때만 보이는 보조 줄(reflow.css)
      el("span", { class: "t-meta" }, categoryTag(task.module_tag, "m-mod"),
        requesterNode(task, "m-dept") || el("span", { class: "m-dept" }, ""))),
    el("td", { class: "c-mod" }, categoryTag(task.module_tag) || "-"),
    el("td", { class: "c-dept" }, requesterNode(task, "") || "-"),
    el("td", { class: "c-last" }, lastMailText(task)),
    el("td", { class: "c-due" }, dueText(task)),
    el("td", { class: "c-st" }, statusBadge(task), followupBadge(task)));
}

// 팔로업 필요(결정기록 0038): 보낸 질문에 설정한 근무일 이상 회신 없음
const isFollowup = (t) => Boolean(t.followup) && t.followup.state === "followup";

// 최근 메일: "11-16 14:05 · 받음/보냄". 내가 마지막으로 회신했으면 '회신함'(완료 후보 판단 보조)
function lastMailText(task) {
  if (!task.last_mail_at) return "-";
  const d = new Date(task.last_mail_at);
  const pad = (n) => String(n).padStart(2, "0");
  const when = pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  const out = task.last_mail_direction === "out";
  const dir = out ? "보냄" : task.last_mail_direction === "in" ? "받음" : "";
  return el("span", { title: "스레드 메일 " + (task.mail_count || 0) + "통 · 마지막 메일 기준" }, when,
    dir ? el("span", { class: out ? "dir-out" : "" }, " · " + dir) : null,
    task.replied_last && task.status !== "done" ? el("span", { class: "replied" }, "회신함") : null);
}

function renderSortHeads() {
  for (const head of document.querySelectorAll("#todo-head th[data-sort]")) {
    const active = todoSort.key === head.dataset.sort;
    head.setAttribute("aria-sort", active ? (todoSort.dir === 1 ? "ascending" : "descending") : "none");
  }
}

// 머리칸 체크박스: 현재 페이지 전체 선택, 일부만 선택이면 indeterminate
function renderHeadCheck() {
  const all = document.getElementById("todo-all");
  const ids = currentView.items.map((t) => t.id);
  const n = ids.filter((id) => checked.has(id)).length;
  all.checked = ids.length > 0 && n === ids.length;
  all.indeterminate = n > 0 && n < ids.length;
}

function renderBulk() {
  const bar = document.getElementById("bulk");
  const ids = [...checked];
  if (!ids.length) { bar.classList.remove("on"); bar.replaceChildren(); return; }
  const hasSuggested = allTasks.some((t) => checked.has(t.id) && t.status === "suggested");
  const run = (action) => () => bulkAction([...checked], action, async () => { checked.clear(); await refresh(); });
  const b = (label, action, cls) => el("button", { type: "button", class: cls || "", onclick: run(action) }, label);
  fill(bar, el("span", { class: "bulk-n" }, ids.length + "건 선택"),
    hasSuggested ? b("승인", "approve", "p") : null,
    hasSuggested ? b("무시", "dismiss") : null,
    b("진행 시작", "in_progress", hasSuggested ? "" : "p"), b("완료 후보로", "done_candidate"), b("보류", "on_hold"),
    el("button", { type: "button", onclick: () => { checked.clear(); renderTodo(); } }, "선택 해제"));
  bar.classList.add("on");
}

function renderTodo() {
  for (const button of document.querySelectorAll("#filters button")) {
    const key = button.dataset.f;
    const count = key === "all" ? allTasks.filter((t) => t.status !== "dismissed").length
      : key === "todo" ? allTasks.filter((t) => t.status !== "dismissed" && t.status !== "suggested").length
      : allTasks.filter((t) => key === "soon" ? Boolean(t.due_badge) : key === "followup" ? isFollowup(t) : t.status === key).length;
    button.textContent = button.textContent.replace(/\s\d+$/, "") + " " + count;
    button.classList.toggle("on", key === currentFilter);
    button.setAttribute("aria-pressed", key === currentFilter ? "true" : "false");
  }
  // 목록에서 사라진 업무는 선택에서 정리한다
  const live = new Set(allTasks.filter((t) => t.status !== "dismissed").map((t) => t.id));
  for (const id of [...checked]) if (!live.has(id)) checked.delete(id);
  currentView = paginate(filteredTasks(), todoPage, perPage);
  todoPage = currentView.page;
  const body = document.getElementById("todo");
  if (currentView.total) body.replaceChildren(...currentView.items.map(todoRow));
  else body.replaceChildren(el("tr", { class: "empty-row" }, el("td", { colspan: "7" }, el("div", { class: "empty" },
    searchTerm ? "검색 결과가 없습니다 · Esc로 지우기" : "해당하는 업무가 없습니다"))));
  renderSortHeads();
  renderHeadCheck();
  renderBulk();
  renderPager(document.getElementById("todo-pager"), document.getElementById("todo-info"), currentView, perPage, (p) => { todoPage = p; renderTodo(); });
  drawDonut(document.getElementById("status-donut"), allTasks, setFilter);
}

// 정리할 제안(나중에로 미룬 것 제외)이 있으면 '제안된 업무'로 안내한다(점검 D3)
function renderSuggestionBanner() {
  const n = allTasks.filter((t) => t.status === "suggested" && !t.snoozed).length;
  document.getElementById("sugg-banner").classList.toggle("hidden", n === 0);
  document.getElementById("sugg-banner-text").textContent = "정리할 제안 " + n + "건 → 제안된 업무에서 정리하기";
}

function setFilter(key) {
  currentFilter = key;
  todoPage = 1;
  renderTodo();
}

// 목록 영역 크기가 바뀌면(창 크기·경고 줄) 페이지 크기를 다시 재고, 보던 첫 항목이 계속 보이게 페이지를 맞춘다.
function fitPageSize() {
  const next = computePerPage(document.getElementById("todo-wrap"), document.getElementById("todo"));
  if (next === perPage) return;
  const firstIndex = (todoPage - 1) * perPage;
  perPage = next;
  todoPage = Math.floor(firstIndex / perPage) + 1;
  renderTodo();
}
let fitFrame = 0;
const scheduleFit = () => { cancelAnimationFrame(fitFrame); fitFrame = requestAnimationFrame(fitPageSize); };
new ResizeObserver(scheduleFit).observe(document.getElementById("todo-wrap"));
window.addEventListener("resize", scheduleFit);
if (document.fonts) document.fonts.ready.then(scheduleFit);

async function loadTasks() {
  const result = await api("GET", "/api/tasks");
  if (result.ok) {
    allTasks = result.data;
    renderTodo();
    scheduleFit();
    renderSuggestionBanner();
  }
  const alerts = await api("GET", "/api/alerts");
  const box = document.getElementById("alerts");
  if (alerts.ok && alerts.data.length) {
    box.replaceChildren(icon("alert-triangle"), "삭제 2일 전 · G-Knowledge 미등록 완료 업무 " + alerts.data.length + "건: ",
      ...alerts.data.map((t) => el("button", { class: "btn sm gk", onclick: () => openGkPreview(t.id, refresh) }, t.title + " 등록")));
    box.classList.remove("hidden");
  } else box.classList.add("hidden");
}

function selectedRow() {
  return selectedId === null ? null : document.querySelector('#todo tr.row[data-id="' + selectedId + '"]');
}

// 행 선택 → 상세 갱신. open이면 좁은 화면에서 서랍을 연다.
async function selectTask(taskId, options) {
  selectedId = taskId;
  renderTodo();
  const row = selectedRow();
  if (row && !(options && options.keepFocus)) row.focus({ preventScroll: true });
  await renderDetail(taskId, refresh);
}

function openTask(taskId, row) {
  selectTask(taskId);
  openDrawer(row);
}

async function refresh() {
  await Promise.all([loadMetrics(), loadTasks()]);
  if (selectedId !== null && allTasks.some((t) => t.id === selectedId)) await selectTask(selectedId, { keepFocus: true });
  else if (selectedId !== null) { selectedId = null; renderDetailEmpty(); }
}

// ---- 키보드(대시보드 전체, 입력칸·모달 밖) ----
function moveSelection(step) {
  const items = currentView.items;
  if (!items.length) return;
  const index = items.findIndex((t) => t.id === selectedId);
  if (index === -1) { selectTask(items[step > 0 ? 0 : items.length - 1].id); return; }
  const next = index + step;
  if (next >= 0 && next < items.length) { selectTask(items[next].id); return; }
  // 페이지 끝에서는 다음·이전 페이지로 넘어가 끝 행을 고른다
  if (step > 0 && currentView.page < currentView.pages) { todoPage += 1; renderTodo(); selectTask(currentView.items[0].id); }
  else if (step < 0 && currentView.page > 1) { todoPage -= 1; renderTodo(); selectTask(currentView.items[currentView.items.length - 1].id); }
}

document.addEventListener("keydown", (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  const target = event.target;
  const typing = target.closest && target.closest("input, textarea, select, [contenteditable='true']");
  const modalOpen = document.querySelector(".dim.show");
  if (event.key === "Escape") {
    if (modalOpen) return;
    if (closeDrawer()) { event.preventDefault(); return; }
    const search = document.querySelector("#search-form input");
    if (searchTerm || (search && search.value)) { search.value = ""; searchTerm = ""; todoPage = 1; renderTodo(); event.preventDefault(); }
    return;
  }
  if (typing || modalOpen || drawerOpen()) return;
  const onControl = target.closest && target.closest("button, a, summary");
  switch (event.key) {
    case "ArrowDown": event.preventDefault(); moveSelection(1); break;
    case "ArrowUp": event.preventDefault(); moveSelection(-1); break;
    case "ArrowRight": event.preventDefault(); todoPage += 1; renderTodo(); break;
    case "ArrowLeft": event.preventDefault(); todoPage -= 1; renderTodo(); break;
    case "Enter":
      if (onControl || selectedId === null) return;
      event.preventDefault(); openDrawer(selectedRow()); break;
    case " ":
      if (onControl || selectedId === null) return;
      event.preventDefault();
      if (checked.has(selectedId)) checked.delete(selectedId); else checked.add(selectedId);
      renderTodo();
      break;
    default:
  }
});

// ---- 상단 검색: 대시보드에서는 입력하는 대로 목록을 거른다(Enter는 기존대로 히스토리 검색) ----
let searchTimer = 0;
const topSearch = document.querySelector("#search-form input");
if (topSearch) {
  topSearch.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { searchTerm = topSearch.value; todoPage = 1; renderTodo(); }, 150);
  });
}

// ---- 이벤트 연결 ----
for (const button of document.querySelectorAll("#seg button")) {
  button.addEventListener("click", () => {
    for (const other of document.querySelectorAll("#seg button")) other.classList.toggle("on", other === button);
    currentGrain = button.dataset.g;
    loadMetrics();
  });
}
for (const button of document.querySelectorAll("#filters button")) {
  button.addEventListener("click", () => setFilter(button.dataset.f));
}
// 정렬 머리칸: 클릭·Enter·Space로 오름 → 내림 → 해제
for (const head of document.querySelectorAll("#todo-head th[data-sort] button")) {
  head.addEventListener("click", () => {
    todoSort = nextSort(todoSort, head.parentElement.dataset.sort);
    todoPage = 1;
    renderTodo();
  });
}
document.getElementById("todo-all").addEventListener("change", (event) => {
  for (const task of currentView.items) {
    if (event.target.checked) checked.add(task.id); else checked.delete(task.id);
  }
  renderTodo();
});
// 표로 보기: 화면을 밀어내지 않도록 모달로 연다
document.getElementById("toggle-table").addEventListener("click", (event) => {
  event.preventDefault();
  openDlg("dlg-mtbl");
  showAiQuality();
});

// AI 품질(최근 30일): 해결 방안 '도움 됨' 비율·AI 호출 성공률(결정기록 0021)
async function showAiQuality() {
  const box = document.getElementById("m-ai");
  const result = await api("GET", "/api/metrics/ai-quality");
  if (!box || !result.ok) return;
  const q = result.data;
  const pct = (v) => (v === null ? "-" : Math.round(v * 100) + "%");
  box.textContent = "AI 품질(최근 " + q.window_days + "일) · 해결 방안 '도움 됨' " + pct(q.helpful_rate)
    + " (" + q.helpful + "/" + (q.helpful + q.not_helpful) + "건" + (q.reference_only ? ", 표본 적음·참고값" : "") + ")"
    + " · AI 호출 성공 " + pct(q.ai_success_rate) + " (" + q.ai_success + "/" + (q.ai_success + q.ai_failure) + "회)";
}
document.getElementById("mtbl-close").addEventListener("click", () => closeDlg("dlg-mtbl"));

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

// 그룹웨어 경로로 수집할 때만 그룹웨어 로그인을 먼저 확인한다(아웃룩 경로면 바로 수집, 결정기록 0034)
function collectMail(body) {
  ensureLogin(["groupware"], () => sendCollect(body), { label: "메일 수집" });
}
async function sendCollect(body) {
  const result = await api("POST", "/api/mail/collect", body);
  if (!result.ok) { toast(errorText(result, "수집하지 못했습니다"), { kind: "error" }); return; }
  toast("새 메일 " + result.data.new_messages + "건 · 제안 " + result.data.suggested + "건 · 제외 " + result.data.excluded_messages + "건");
  await loadSyncStatus();
  await refresh();
}
document.getElementById("btn-collect").addEventListener("click", () => collectMail({ period: "sync" }));
document.getElementById("btn-range-toggle").addEventListener("click", (event) => {
  const form = document.getElementById("range-form");
  form.classList.toggle("hidden");
  event.currentTarget.setAttribute("aria-expanded", form.classList.contains("hidden") ? "false" : "true");
  if (!form.classList.contains("hidden")) document.getElementById("range-start").focus();
});
document.getElementById("range-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const start = document.getElementById("range-start").value;
  const end = document.getElementById("range-end").value;
  if (!start || !end) { toast("시작일과 종료일을 고르세요", { kind: "warn" }); return; }
  document.getElementById("range-form").classList.add("hidden");
  collectMail({ period: "range", start: start, end: end });
});
document.getElementById("btn-manual").addEventListener("click", async () => {
  const title = window.prompt("업무 제목을 입력하세요 (200자 이내)");
  if (!title) return;
  const result = await api("POST", "/api/tasks", { title: title.slice(0, 200) });
  toast(result.ok ? "업무를 추가했습니다" : errorText(result, "추가하지 못했습니다"), { kind: result.ok ? "ok" : "error" });
  await refresh();
});

loadSyncStatus();
renderDetailEmpty();

// 작업 센터에서 등록 상태가 바뀌면 열린 상세를 다시 그린다(결정기록 0035)
document.addEventListener("jobs-changed", (event) => {
  if (selectedId === null) return;
  if (event.detail.some((job) => job.kind === "gk" && job.task_id === selectedId)) selectTask(selectedId, { keepFocus: true });
});
document.addEventListener("gk-changed", (event) => { if (event.detail.taskId === selectedId) selectTask(selectedId, { keepFocus: true }); });
// 다른 화면의 작업 센터에서 '다시 시도'를 누르면 ?gk=업무번호로 와서 그 업무의 등록 창을 연다
(() => {
  const params = new URLSearchParams(location.search);
  const id = Number(params.get("gk"));
  if (!Number.isInteger(id) || id <= 0) return;
  history.replaceState(null, "", location.pathname);
  selectTask(id).then(() => (params.get("retry") ? retryGkUnknown(id, refresh) : openGkPreview(id, refresh)));
})();
// 업무 구분 색(전자결재·기본 구분)을 정한 뒤 목록을 그린다. 읽지 못해도 기본값으로 그린다
loadCategoryKinds().finally(refresh);

// 상세 패널 크기 조절(결정기록 0016). 폭이 바뀌면 #todo-wrap의 ResizeObserver가 행 수를 다시 계산한다
initSplitter({ handle: document.getElementById("split-detail"), panel: document.getElementById("detail"), other: document.querySelector(".todo-card"),
  cssVar: "--detail-w", prefKey: "detail_w", side: "right", min: 280, maxRatio: 0.6, otherMin: 520 });
