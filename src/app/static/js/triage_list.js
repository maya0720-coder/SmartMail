// 제안된 업무 트리아지 — 목록(결정기록 0015, 명세 2-1).
// 1단계: 목록·선택·키보드(↑↓ · A·S·D·E). 2단계: 받은 시각 묶음·탭·진행 막대·'나중에 n건' 펼침.
// 여러 건 체크·일괄 작업 바는 triage_bulk.js(결정기록 0036·0040).
// 목록 상태는 메모리에만 둔다. 모든 글자는 el()/textContent로만 넣는다.
"use strict";

const TR = {
  items: [], snoozedItems: [], selectedId: null, categories: [],
  summary: { visible: 0, snoozed: 0, decided_today: 0 },
  tab: "all", snoozedOpen: false, page: 1,
};
// 한 페이지에 보여 줄 제안 수(결정기록 0023). 목록은 패널 안에서 스크롤되므로 높이와 무관한 고정값
const TRIAGE_PAGE_SIZE = 20;

// 이유 코드 → 화면 문구(명세 3-2)
const REASON_LABELS = {
  request_phrase: "요청 표현이 있음",
  subject_request: "제목이 요청 형태",
  due_phrase: "기한 표현이 있음",
  to_me: "받는 사람: 나",
  cc_only: "참조(CC)로 받음",
  thread_waiting: "대화가 이어짐 · 내 회신 대기",
  approval: "결재 키워드",
  ai_judged: "AI가 업무로 판단",
  learned_penalty: "비슷한 메일을 '업무 아님'으로 고른 적 있음",
  ai_unavailable: "AI 판단 실패 · 직접 확인 필요",
};
const GROUP_LABELS = { today: "오늘", yesterday: "어제", week: "이번 주", older: "이전" };

function confidenceBar(value) {
  const on = value === null || value === undefined ? 0 : Math.max(1, Math.ceil(value * 4));
  const label = value === null || value === undefined ? "확신도 정보 없음" : "확신도 " + Math.round(value * 100) + "%";
  return el("span", { class: "conf", title: label, "aria-label": label },
    ...[1, 2, 3, 4].map((n) => el("i", { class: n <= on ? "on" : "" })));
}

function dueChip(task) {
  if (!task.due_at) return null;
  const date = task.due_at.slice(5, 10).replace("-", "/");
  const days = (new Date(task.due_at) - new Date()) / 86400000;
  const cls = days < 0 ? "due late" : days <= 3 ? "due soon" : "due";
  return el("span", { class: "tchip " + cls }, (days < 0 ? "지남 " : "~") + date);
}

function categoryChip(task) {
  if (!task.module_tag) return null;
  return el("span", { class: "tchip cat" + (task.reasons.includes("approval") ? " approval" : "") }, task.module_tag);
}

function receivedLabel(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const hm = iso.slice(11, 16);
  return d.toDateString() === new Date().toDateString() ? hm : iso.slice(5, 10).replace("-", "/") + " " + hm;
}

// ---- 탭(전체 · 구분별 · 확신도 낮음) ----
function inTab(task, tab) {
  if (tab === "all") return true;
  if (tab === "low") return task.low_confidence;
  return task.module_tag === tab.slice(4);  // "cat:<구분>"
}

// 화면은 받은 시각 묶음(오늘·어제·이번 주·이전) 순서로 그리므로 순서도 묶음 기준으로 맞춘다
// (Shift 범위·↑↓·페이지 나누기가 보이는 순서와 같아야 한다. 묶음 안에서는 기존 순서 유지)
const GROUP_RANK = { today: 0, yesterday: 1, week: 2, older: 3 };
function filteredItems() {
  const rank = (t) => GROUP_RANK[t.group || "older"] ?? 3;
  return TR.items.filter((t) => inTab(t, TR.tab)).sort((a, b) => rank(a) - rank(b));
}

// 현재 페이지(페이지 번호는 범위 안으로 맞춘다)
function pageView() {
  const view = paginate(filteredItems(), TR.page, TRIAGE_PAGE_SIZE);
  TR.page = view.page;
  return view;
}

// 펼친 '나중에' 목록은 마지막 페이지 끝에만 보인다
function onLastPage() {
  return TR.page === pageView().pages;
}

// 지금 화면에 보이는 항목 순서(현재 페이지 → 마지막 페이지면 펼친 '나중에' 목록)
function visibleOrder() {
  return [...pageView().items, ...(TR.snoozedOpen && onLastPage() ? TR.snoozedItems : [])];
}

// ↑↓·결정 후 다음 항목 선택에 쓰는 전체 순서(모든 페이지)
function navOrder() {
  return [...filteredItems(), ...(TR.snoozedOpen ? TR.snoozedItems : [])];
}

// 항목이 있는 페이지 번호(펼친 '나중에' 항목은 마지막 페이지)
function pageOf(id) {
  const index = filteredItems().findIndex((t) => t.id === id);
  if (index < 0) return paginate(filteredItems(), Number.MAX_SAFE_INTEGER, TRIAGE_PAGE_SIZE).page;
  return Math.floor(index / TRIAGE_PAGE_SIZE) + 1;
}

function goPage(page) {
  TR.page = page;
  renderItems();
  document.getElementById("tr-items").scrollTop = 0;
  ensureSelection();
}

function findTask(id) {
  return TR.items.find((t) => t.id === id) || TR.snoozedItems.find((t) => t.id === id);
}

function renderTabs() {
  const tabs = [["all", "전체"], ...TR.categories.map((c) => ["cat:" + c, c]), ["low", "확신도 낮음"]];
  fill(document.getElementById("tr-tabs"), ...tabs.map(([key, label]) => {
    const n = TR.items.filter((t) => inTab(t, key)).length;
    return el("button", { type: "button", class: key === TR.tab ? "on" : "", "aria-pressed": key === TR.tab ? "true" : "false",
      onclick: () => { TR.tab = key; TR.page = 1; renderTabs(); renderItems(); ensureSelection(); bulkChanged(); } }, label + " " + n);
  }));
}

function renderCount() {
  const s = TR.summary;
  document.getElementById("tr-count").textContent = "정리할 제안 " + s.visible + "건";
  // 오늘 정리 진행 막대: 오늘 결정 수 / (오늘 결정 + 남은 제안)
  const total = s.decided_today + s.visible;
  const pct = total ? Math.round((s.decided_today / total) * 100) : 100;
  const bar = document.getElementById("tr-progress");
  bar.setAttribute("aria-valuenow", String(pct));
  bar.title = "오늘 정리 " + s.decided_today + "/" + total;
  document.getElementById("tr-progress-bar").style.width = pct + "%";
  const link = document.getElementById("tr-snoozed");
  link.classList.toggle("hidden", !s.snoozed);
  link.textContent = (TR.snoozedOpen ? "▾ " : "▸ ") + "나중에 " + s.snoozed + "건(다음 근무일 아침)";
  link.setAttribute("aria-expanded", TR.snoozedOpen ? "true" : "false");
}

function itemNode(task, snoozedItem) {
  const sel = task.id === TR.selectedId;
  const picked = BULK.ids.has(task.id);
  // 일괄 선택 체크박스: 마우스로만 누른다(키보드는 Space·Shift+↑↓, 결정기록 0036)
  const check = el("input", { type: "checkbox", class: "tr-check", tabindex: "-1", checked: picked,
    "aria-label": "일괄 선택: " + task.title });
  check.addEventListener("click", (event) => { event.stopPropagation(); toggleBulk(task.id); });
  return el("li", { class: "tr-item" + (sel ? " sel" : "") + (picked ? " picked" : "") + (snoozedItem ? " snoozed" : ""), role: "option",
    "aria-selected": sel ? "true" : "false", dataset: { id: String(task.id) }, tabindex: "-1",
    onmousedown: (event) => { if (event.shiftKey) event.preventDefault(); },  // Shift+클릭 때 글자 선택 막기
    onclick: (event) => { if (!bulkClick(event, task.id)) selectTriage(task.id, true, true); } },
    el("div", { class: "tr-line1" }, check, summaryChip(task.summary_state), el("span", { class: "tr-title", title: task.title }, task.title), confidenceBar(task.confidence)),
    el("div", { class: "tr-line2" }, categoryChip(task), dueChip(task),
      requesterNode(task, "dept"),
      task.thread_count >= 2 ? el("span", { class: "thr", title: "대화 " + task.thread_count + "통" }, icon("mail"), String(task.thread_count)) : null,
      snoozedItem && task.snooze_until ? receivedNode(task.snooze_until, "다시 보임 ") : receivedNode(task.requested_at, "")));
}

// 받은 시각: 날짜와 시각을 나눠 둔다(목록이 좁으면 날짜만 보여 줄바꿈을 막는다, triage.css)
function receivedNode(iso, prefix) {
  const label = receivedLabel(iso);
  const hasDate = label.includes(" ");
  return el("span", { class: "tm" + (hasDate ? " has-date" : "") }, prefix,
    hasDate ? el("span", { class: "tm-d" }, label.split(" ")[0]) : null, el("span", { class: "tm-t" }, (hasDate ? " " : "") + label.split(" ").pop()));
}

function renderTriagePager(view) {
  document.getElementById("tr-pg").classList.toggle("hidden", !view.total);
  renderPager(document.getElementById("tr-pager"), document.getElementById("tr-pg-info"), view, TRIAGE_PAGE_SIZE, goPage);
}

function renderItems() {
  const list = document.getElementById("tr-items");
  const view = pageView();
  const items = view.items;
  const lastPage = TR.page === view.pages;
  renderTriagePager(view);
  if (!TR.items.length && !(TR.snoozedOpen && TR.snoozedItems.length)) {
    list.replaceChildren(el("li", { class: "tr-empty" },
      icon("check"), el("div", { class: "k" }, "정리할 제안이 없습니다"),
      el("div", { class: "t-sub", id: "tr-last-sync" }, ""),
      el("button", { type: "button", class: "btn sm", onclick: collectFromEmpty }, icon("refresh-cw"), "메일 새로고침")));
    loadLastSync();
    return;
  }
  const nodes = [];
  for (const group of ["today", "yesterday", "week", "older"]) {
    const inGroup = items.filter((t) => (t.group || "older") === group);
    if (!inGroup.length) continue;
    nodes.push(el("li", { class: "tr-group", role: "presentation" }, groupCheck(group, GROUP_LABELS[group]), GROUP_LABELS[group] + " " + inGroup.length));
    nodes.push(...inGroup.map((t) => itemNode(t, false)));
  }
  if (!items.length) nodes.push(el("li", { class: "tr-group-empty", role: "presentation" }, "이 탭에 해당하는 제안이 없습니다"));
  if (TR.snoozedOpen && TR.snoozedItems.length && lastPage) {
    nodes.push(el("li", { class: "tr-group", role: "presentation" }, groupCheck("snoozed", "나중에로 미룬 제안"), "나중에로 미룬 제안 " + TR.snoozedItems.length));
    nodes.push(...TR.snoozedItems.map((t) => itemNode(t, true)));
  }
  list.replaceChildren(...nodes);
  syncBulkMarks();
}

async function loadLastSync() {
  const result = await api("GET", "/api/mail/sync-status");
  const box = document.getElementById("tr-last-sync");
  if (box && result.ok && result.data.last_synced_at) {
    box.textContent = "마지막 메일 수집 " + result.data.last_synced_at.slice(5, 16).replace("T", " ");
  }
}

function collectFromEmpty() {
  ensureLogin(["groupware"], sendCollectFromEmpty, { label: "메일 수집" });  // 그룹웨어 경로일 때만 로그인(0034)
}
async function sendCollectFromEmpty() {
  const result = await api("POST", "/api/mail/collect", { period: "sync" });
  toast(result.ok ? "새 제안 " + result.data.suggested + "건" : errorText(result, "수집하지 못했습니다"), { kind: result.ok ? "ok" : "error" });
  loadTriage();
}

const byReceived = (a, b) => String(b.requested_at || "").localeCompare(String(a.requested_at || "")) || b.id - a.id;

async function loadTriage(keepId) {
  if (!TR.categories.length) {
    const cats = await api("GET", "/api/task-categories");
    TR.categories = cats.ok ? cats.data.categories : [];
  }
  const [list, summary, snoozed] = await Promise.all([
    api("GET", "/api/tasks?status=suggested"),
    api("GET", "/api/tasks/suggested/summary"),
    TR.snoozedOpen ? api("GET", "/api/tasks?status=suggested&snoozed=only") : Promise.resolve(null),
  ]);
  if (!list.ok) return;
  TR.items = list.data.slice().sort(byReceived);
  TR.snoozedItems = snoozed && snoozed.ok ? snoozed.data.slice().sort(byReceived) : [];
  if (summary.ok) TR.summary = summary.data;
  renderCount();
  renderTabs();
  const want = keepId !== undefined ? keepId : TR.selectedId;
  const found = navOrder().find((t) => t.id === want);
  if (found && keepId !== undefined) TR.page = pageOf(found.id);  // 지정한 항목이 있는 페이지로
  const target = found || visibleOrder()[0];
  TR.selectedId = target ? target.id : null;
  pruneBulk();
  renderItems();
  renderBulkBar();  // 체크가 있으면 상세 위에 일괄 바(상세는 그대로, 결정기록 0040)
  if (target) renderTriageDetail(target); else renderTriageEmpty();
}

function ensureSelection() {
  const order = visibleOrder();
  if (order.some((t) => t.id === TR.selectedId)) return;
  if (order.length) selectTriage(order[0].id, false); else { TR.selectedId = null; renderTriageEmpty(); }
}

// open: 좁은 화면(≤900px)에서 상세 서랍을 연다(목록에서 항목을 눌렀을 때)
function selectTriage(id, focus, open) {
  const task = findTask(id);
  if (!task) return;
  TR.selectedId = id;
  let row = null;
  for (const li of document.querySelectorAll("#tr-items .tr-item")) {
    const on = Number(li.dataset.id) === id;
    li.classList.toggle("sel", on);
    li.setAttribute("aria-selected", on ? "true" : "false");
    if (on) { row = li; li.scrollIntoView({ block: "nearest" }); if (focus) li.focus({ preventScroll: true }); }
  }
  renderTriageDetail(task);
  if (open) openTriageDrawer(row);
}

function moveTriage(step) {
  const order = navOrder();
  if (!order.length) return;
  const index = order.findIndex((t) => t.id === TR.selectedId);
  const next = order[Math.min(order.length - 1, Math.max(0, index + step))];
  // 페이지 경계를 넘으면 페이지도 넘긴다
  const page = pageOf(next.id);
  if (page !== TR.page) { TR.page = page; renderItems(); }
  selectTriage(next.id, true, false);
}

// 결정한 항목을 왼쪽으로 빼며 지우고(200ms) 다음 항목을 고른다
function removeTriageItem(id) {
  const order = navOrder();
  const index = order.findIndex((t) => t.id === id);
  const wasSnoozed = TR.snoozedItems.some((t) => t.id === id);
  TR.items = TR.items.filter((t) => t.id !== id);
  TR.snoozedItems = TR.snoozedItems.filter((t) => t.id !== id);
  if (wasSnoozed) TR.summary.snoozed = Math.max(0, TR.summary.snoozed - 1);
  else TR.summary.visible = Math.max(0, TR.summary.visible - 1);
  TR.summary.decided_today += 1;
  renderCount();
  renderTabs();
  const rest = navOrder();
  const next = rest[Math.min(Math.max(index, 0), rest.length - 1)];
  const li = document.querySelector('#tr-items .tr-item[data-id="' + id + '"]');
  const finish = () => {
    TR.selectedId = next ? next.id : null;
    if (next) TR.page = pageOf(next.id);  // 빠진 만큼 당겨진 다음 항목이 있는 페이지(범위 밖이면 자동 보정)
    renderItems();
    if (next) selectTriage(next.id, true, false); else renderTriageEmpty();
  };
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (li && !reduce) { li.classList.add("leaving"); setTimeout(finish, 200); } else finish();
}

document.getElementById("tr-snoozed").addEventListener("click", async () => {
  TR.snoozedOpen = !TR.snoozedOpen;
  await loadTriage();
});

document.addEventListener("keydown", (event) => {
  if (event.altKey) return;
  if (typeof QUICK !== "undefined" && QUICK.active) return;  // 빠른 정리 모드가 키를 쓴다(3단계)
  const target = event.target;
  if (target.closest && target.closest("input, textarea, select, [contenteditable='true']")) return;
  if (document.querySelector(".dim.show")) return;
  if (handleBulkKey(event)) { event.preventDefault(); return; }  // 여러 건 선택(결정기록 0036)
  if (event.ctrlKey || event.metaKey) return;
  if (event.key === "Escape" && closeTriageDrawer()) { event.preventDefault(); return; }
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  const actions = {
    ArrowDown: () => moveTriage(1),
    ArrowUp: () => moveTriage(-1),
    a: () => decideTriage("approve"),
    s: () => decideTriage("snooze"),
    d: () => decideTriage("dismiss"),
    e: () => startTitleEdit(),
    w: () => toggleTriageWide(),  // 상세 넓게 보기(결정기록 0033)
    q: () => { if (typeof startQuickMode === "function") startQuickMode(); },
    Enter: () => openTriageDrawer(document.querySelector("#tr-items .tr-item.sel")),
  };
  if (actions[key]) { event.preventDefault(); actions[key](); }
});

// 요약 진행 중 칩·제목만 바꾼다(결정기록 0029). 목록을 다시 그리지 않아 선택·스크롤이 유지된다.
async function patchSummaryStates() {
  const list = await api("GET", "/api/tasks?status=suggested");
  if (!list.ok) return;
  const fresh = new Map(list.data.map((t) => [t.id, t]));
  for (const task of TR.items) {
    const now = fresh.get(task.id);
    if (!now || (now.summary_state === task.summary_state && now.title === task.title)) continue;
    task.summary_state = now.summary_state;
    task.summary_pending = now.summary_pending;
    task.title = now.title;
    const node = document.querySelector('#tr-items li[data-id="' + task.id + '"] .tr-line1');
    if (!node) continue;
    const old = node.querySelector(".sum-chip");
    const chip = summaryChip(task.summary_state);
    if (old && chip) old.replaceWith(chip); else if (old) old.remove(); else if (chip) node.prepend(chip);
    const title = node.querySelector(".tr-title");
    if (title) { title.textContent = task.title; title.title = task.title; }
    if (task.id === TR.selectedId && typeof refreshDetailTitle === "function") refreshDetailTitle(task);
  }
}
