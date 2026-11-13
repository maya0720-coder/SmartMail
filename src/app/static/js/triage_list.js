// 제안된 업무 트리아지 — 목록(결정기록 0015, 명세 2-1).
// 1단계: 목록·선택·키보드(↑↓ · A·S·D·E). 2단계: 받은 시각 묶음·탭·진행 막대·'나중에 n건' 펼침.
// 목록 상태는 메모리에만 둔다. 모든 글자는 el()/textContent로만 넣는다.
"use strict";

const TR = {
  items: [], snoozedItems: [], selectedId: null, categories: [],
  summary: { visible: 0, snoozed: 0, decided_today: 0 },
  tab: "all", snoozedOpen: false,
};

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

function filteredItems() {
  return TR.items.filter((t) => inTab(t, TR.tab));
}

// ↑↓·다음 항목 선택에 쓰는 화면 순서(거른 목록 → 펼친 '나중에' 목록)
function visibleOrder() {
  return [...filteredItems(), ...(TR.snoozedOpen ? TR.snoozedItems : [])];
}

function findTask(id) {
  return TR.items.find((t) => t.id === id) || TR.snoozedItems.find((t) => t.id === id);
}

function renderTabs() {
  const tabs = [["all", "전체"], ...TR.categories.map((c) => ["cat:" + c, c]), ["low", "확신도 낮음"]];
  fill(document.getElementById("tr-tabs"), ...tabs.map(([key, label]) => {
    const n = TR.items.filter((t) => inTab(t, key)).length;
    return el("button", { type: "button", class: key === TR.tab ? "on" : "", "aria-pressed": key === TR.tab ? "true" : "false",
      onclick: () => { TR.tab = key; renderTabs(); renderItems(); ensureSelection(); } }, label + " " + n);
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
  return el("li", { class: "tr-item" + (sel ? " sel" : "") + (snoozedItem ? " snoozed" : ""), role: "option",
    "aria-selected": sel ? "true" : "false", dataset: { id: String(task.id) }, tabindex: "-1",
    onclick: () => selectTriage(task.id, true, true) },
    el("div", { class: "tr-line1" }, el("span", { class: "tr-title", title: task.title }, task.title), confidenceBar(task.confidence)),
    el("div", { class: "tr-line2" }, categoryChip(task), dueChip(task),
      task.requester_dept ? el("span", { class: "dept" }, task.requester_dept) : null,
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

function renderItems() {
  const list = document.getElementById("tr-items");
  const items = filteredItems();
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
    nodes.push(el("li", { class: "tr-group", role: "presentation" }, GROUP_LABELS[group] + " " + inGroup.length));
    nodes.push(...inGroup.map((t) => itemNode(t, false)));
  }
  if (!items.length) nodes.push(el("li", { class: "tr-group-empty", role: "presentation" }, "이 탭에 해당하는 제안이 없습니다"));
  if (TR.snoozedOpen && TR.snoozedItems.length) {
    nodes.push(el("li", { class: "tr-group", role: "presentation" }, "나중에로 미룬 제안 " + TR.snoozedItems.length));
    nodes.push(...TR.snoozedItems.map((t) => itemNode(t, true)));
  }
  list.replaceChildren(...nodes);
}

async function loadLastSync() {
  const result = await api("GET", "/api/mail/sync-status");
  const box = document.getElementById("tr-last-sync");
  if (box && result.ok && result.data.last_synced_at) {
    box.textContent = "마지막 메일 수집 " + result.data.last_synced_at.slice(5, 16).replace("T", " ");
  }
}

async function collectFromEmpty() {
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
  const order = visibleOrder();
  const want = keepId !== undefined ? keepId : TR.selectedId;
  const target = order.find((t) => t.id === want) || order[0];
  TR.selectedId = target ? target.id : null;
  renderItems();
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
  const order = visibleOrder();
  if (!order.length) return;
  const index = order.findIndex((t) => t.id === TR.selectedId);
  const next = Math.min(order.length - 1, Math.max(0, index + step));
  selectTriage(order[next].id, true, false);
}

// 결정한 항목을 왼쪽으로 빼며 지우고(200ms) 다음 항목을 고른다
function removeTriageItem(id) {
  const order = visibleOrder();
  const index = order.findIndex((t) => t.id === id);
  const wasSnoozed = TR.snoozedItems.some((t) => t.id === id);
  TR.items = TR.items.filter((t) => t.id !== id);
  TR.snoozedItems = TR.snoozedItems.filter((t) => t.id !== id);
  if (wasSnoozed) TR.summary.snoozed = Math.max(0, TR.summary.snoozed - 1);
  else TR.summary.visible = Math.max(0, TR.summary.visible - 1);
  TR.summary.decided_today += 1;
  renderCount();
  renderTabs();
  const rest = visibleOrder();
  const next = rest[Math.min(Math.max(index, 0), rest.length - 1)];
  const li = document.querySelector('#tr-items .tr-item[data-id="' + id + '"]');
  const finish = () => {
    TR.selectedId = next ? next.id : null;
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
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (typeof QUICK !== "undefined" && QUICK.active) return;  // 빠른 정리 모드가 키를 쓴다(3단계)
  const target = event.target;
  if (target.closest && target.closest("input, textarea, select, [contenteditable='true']")) return;
  if (document.querySelector(".dim.show")) return;
  if (event.key === "Escape" && closeTriageDrawer()) { event.preventDefault(); return; }
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  const actions = {
    ArrowDown: () => moveTriage(1),
    ArrowUp: () => moveTriage(-1),
    a: () => decideTriage("approve"),
    s: () => decideTriage("snooze"),
    d: () => decideTriage("dismiss"),
    e: () => startTitleEdit(),
    q: () => { if (typeof startQuickMode === "function") startQuickMode(); },
    Enter: () => openTriageDrawer(document.querySelector("#tr-items .tr-item.sel")),
  };
  if (actions[key]) { event.preventDefault(); actions[key](); }
});
