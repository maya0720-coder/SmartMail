// 제안 일괄 처리(U2, TODO-02, 결정기록 0036·0040).
// - 체크(일괄 대상)는 '선택'(상세 1건)과 분리한다. 일반 클릭은 상세만 바꾸고 체크는 그대로 둔다.
//   체크박스·Space 토글 · Ctrl/⌘+클릭 토글 · Shift+클릭 범위(현재 페이지 안) · Ctrl+A 현재 페이지 · Esc 전체 해제
// - 목록 머리 3상태 체크(현재 페이지) + ▾(이 탭 전체·모든 탭 전체·전체 해제), 묶음 머리 3상태 체크(현재 탭 모든 페이지)
// - 체크가 1건 이상이면 상세 위에 일괄 작업 바(상세는 그대로). 탭·페이지를 바꿔도 체크는 유지한다
// 공통 함수는 bulk_select.js. 체크 상태는 메모리에만 둔다. 모든 글자는 el()/textContent로만 넣는다.
"use strict";

const BULK = { ids: new Set(), anchor: null };
const BULK_UNDO_MS = 10000;
const BULK_DONE = {
  approve: "할 일로 옮겼습니다", snooze: "나중에로 미뤘습니다",
  dismiss: "업무 아님으로 정리했습니다", category: "구분을 바꿨습니다",
};

function bulkActive() { return BULK.ids.size >= 1; }

// 묶음(받은 시각) 항목: 현재 탭의 모든 페이지. 'snoozed'는 펼친 '나중에' 목록
function groupIds(group) {
  if (group === "snoozed") return TR.snoozedItems.map((t) => t.id);
  return filteredItems().filter((t) => (t.group || "older") === group).map((t) => t.id);
}
const pageIds = () => visibleOrder().map((t) => t.id);

// 목록 행·머리·묶음 체크 표시만 바꾼다(목록을 다시 그리지 않음)
function syncBulkMarks() {
  document.getElementById("tr-items").classList.toggle("multi", BULK.ids.size > 0);
  for (const li of document.querySelectorAll("#tr-items .tr-item")) {
    const on = BULK.ids.has(Number(li.dataset.id));
    li.classList.toggle("picked", on);
    const box = li.querySelector(".tr-check");
    if (box) box.checked = on;
  }
  for (const box of document.querySelectorAll("#tr-items .tr-group-check")) headState(box, groupState(BULK.ids, groupIds(box.dataset.group)));
  const head = document.getElementById("tr-all");
  if (head) { headState(head, groupState(BULK.ids, pageIds())); head.disabled = !pageIds().length; }
  const info = document.getElementById("tr-checked");
  if (info) info.textContent = BULK.ids.size ? "체크 " + BULK.ids.size + "건" : "";
}

// 체크가 바뀐 뒤: 표시와 일괄 바만 갱신한다(상세는 그대로)
function bulkChanged() {
  syncBulkMarks();
  renderBulkBar();
}

function clearBulk() {
  if (!BULK.ids.size) return false;
  BULK.ids.clear();
  BULK.anchor = null;
  bulkChanged();
  return true;
}

function toggleBulk(id) {
  toggleOne(BULK.ids, id);
  BULK.anchor = id;
  bulkChanged();
}

// Shift 범위 체크: 현재 페이지에 보이는 항목 안에서만(페이지 경계를 넘지 않음). 기존 체크는 유지
function rangeBulk(id) {
  const order = pageIds();
  const from = order.indexOf(BULK.anchor !== null ? BULK.anchor : TR.selectedId);
  const to = order.indexOf(id);
  if (to < 0) return;
  const [lo, hi] = from < 0 ? [to, to] : [Math.min(from, to), Math.max(from, to)];
  setMany(BULK.ids, order.slice(lo, hi + 1), true);
  bulkChanged();
}

function checkMany(ids) {
  setMany(BULK.ids, ids, true);
  BULK.anchor = null;
  bulkChanged();
}
function selectBulkPage() { checkMany(pageIds()); }
function selectBulkTab() { checkMany(filteredItems().map((t) => t.id)); }
function selectBulkAllTabs() { checkMany(TR.items.map((t) => t.id)); }

// 목록 머리 체크(현재 페이지 3상태)·▾ 메뉴
function toggleBulkHead() {
  toggleGroup(BULK.ids, pageIds());
  bulkChanged();
}
function openTriageBulkMenu(event) {
  event.stopPropagation();
  if (closeBulkMenu()) return;
  const tab = filteredItems().length;
  const all = TR.items.length;
  openBulkMenu(event.currentTarget, [
    ["이 탭 전체 선택(" + tab + "건)", selectBulkTab, !tab],
    ["모든 탭 전체 선택(" + all + "건)", selectBulkAllTabs, !all],
    ["전체 해제", clearBulk, !BULK.ids.size],
  ]);
  const menu = document.getElementById("bulk-menu");
  const ids = ["bulk-tab-all", "bulk-all-tabs", "bulk-none"];
  [...menu.children].forEach((button, n) => { button.id = ids[n]; });
}

// 묶음 머리 체크박스(triage_list.js renderItems에서 부름)
function groupCheck(group, label) {
  const box = el("input", { type: "checkbox", class: "tr-group-check", tabindex: "-1", dataset: { group },
    "aria-label": label + " 묶음 전체 선택" });
  box.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleGroup(BULK.ids, groupIds(group));
    bulkChanged();
  });
  return box;
}

// 목록 행 클릭(triage_list.js에서 부름). 체크를 바꿨으면 true(상세는 바꾸지 않음)
function bulkClick(event, id) {
  if (event.shiftKey) { event.preventDefault(); rangeBulk(id); return true; }
  if (event.ctrlKey || event.metaKey) { event.preventDefault(); toggleBulk(id); return true; }
  BULK.anchor = id;  // 일반 클릭: 체크는 그대로, 범위 시작점만 옮긴다
  return false;
}

// 키보드 범위 넓히기: Shift+↑↓는 지금 항목과 옮긴 다음 항목을 체크한다(현재 페이지 안)
function extendBulk(step) {
  const order = pageIds();
  const index = order.indexOf(TR.selectedId);
  const next = order[index + step];
  if (index < 0 || next === undefined) return;
  setMany(BULK.ids, [TR.selectedId, next], true);
  selectTriage(next, true, false);
  bulkChanged();
}

// triage_list.js의 keydown에서 먼저 부른다. 처리했으면 true
function handleBulkKey(event) {
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  if (event.ctrlKey || event.metaKey) {
    if (key === "a") { selectBulkPage(); return true; }
    return false;
  }
  if (event.shiftKey && (key === "ArrowDown" || key === "ArrowUp")) { extendBulk(key === "ArrowDown" ? 1 : -1); return true; }
  if (key === " " && !(event.target.closest && event.target.closest("button, a"))) {
    if (TR.selectedId !== null) toggleBulk(TR.selectedId);
    return true;
  }
  if (!BULK.ids.size) return false;
  if (key === "Escape") return clearBulk();
  // 체크가 있으면 A·S·D는 체크한 항목에 적용한다(일괄 바에 표시)
  const actions = { a: "approve", s: "snooze", d: "dismiss" };
  if (actions[key]) { bulkDecide(actions[key]); return true; }
  return false;
}

// 상세 위 일괄 작업 바(대시보드 #bulk와 같은 모양·문구)
function renderBulkBar() {
  const bar = document.getElementById("tr-bulk");
  const n = BULK.ids.size;
  if (!n) { bar.classList.remove("on"); bar.replaceChildren(); return; }
  const inTabIds = new Set(filteredItems().map((t) => t.id).concat(TR.snoozedItems.map((t) => t.id)));
  const outside = [...BULK.ids].filter((id) => !inTabIds.has(id)).length;
  const cat = el("select", { id: "bulk-cat", "aria-label": "바꿀 구분" }, ...TR.categories.map((c) => el("option", { value: c }, c)));
  fill(bar, el("div", { id: "bulk-bar", class: "bulk-in" },
    el("span", { class: "bulk-n", id: "bulk-count" }, n + "건 선택" + (outside ? "(다른 탭 포함)" : "")),
    el("button", { type: "button", class: "p", id: "bulk-approve", title: "A", onclick: () => bulkDecide("approve") }, "할 일로"),
    el("button", { type: "button", id: "bulk-snooze", title: "S", onclick: () => bulkDecide("snooze") }, "나중에"),
    el("button", { type: "button", id: "bulk-dismiss", title: "D", onclick: () => bulkDecide("dismiss") }, "업무 아님"),
    TR.categories.length ? cat : null,
    TR.categories.length ? el("button", { type: "button", id: "bulk-cat-apply", onclick: () => bulkDecide("category", cat.value) }, "구분 변경") : null,
    el("button", { type: "button", id: "bulk-clear", title: "Esc", onclick: clearBulk }, "선택 해제")),
    el("span", { class: "bulk-hint" }, "A·S·D 키는 체크한 항목에 적용 · 최대 " + BULK_LIMIT + "건"));
  bar.classList.add("on");
}

async function bulkDecide(decision, moduleTag) {
  const ids = [...BULK.ids];
  if (!ids.length) return;
  const body = { ids, decision };
  if (decision === "category") body.module_tag = moduleTag;
  const result = await api("POST", "/api/triage/bulk", body);
  if (!result.ok) { toast(errorText(result, "처리하지 못했습니다"), { kind: "error" }); return; }
  const done = result.data.succeeded.length;
  const failed = result.data.failed.length;
  const token = result.data.bulk_token;
  // 성공한 항목은 체크에서 빼고(구분 변경은 목록에 남으므로 그대로 해제), 실패한 건은 체크로 남긴다
  BULK.ids = new Set(result.data.failed.map((f) => f.id));
  BULK.anchor = null;
  const text = done + "건을 " + BULK_DONE[decision] + (failed ? " · " + failed + "건은 처리하지 못했습니다" : "");
  toast(text, token ? { kind: failed ? "warn" : "ok", action: "되돌리기", ms: BULK_UNDO_MS, onAction: () => revertBulk(token) }
    : { kind: failed ? "warn" : "ok" });
  await loadTriage();
  bulkChanged();
}

async function revertBulk(token) {
  const result = await api("POST", "/api/triage/bulk/revert", { bulk_token: token });
  if (!result.ok) { toast(errorText(result, "되돌리지 못했습니다"), { kind: "warn" }); return; }
  const failed = result.data.failed.length;
  toast(result.data.reverted.length + "건을 되돌렸습니다" + (failed ? " · " + failed + "건은 되돌리지 못했습니다" : ""),
    { kind: failed ? "warn" : "ok" });
  await loadTriage();
}

// 목록을 다시 불러온 뒤 사라진 항목만 체크에서 뺀다(triage_list.js loadTriage에서 부름)
function pruneBulk() {
  for (const id of [...BULK.ids]) if (!findTask(id)) BULK.ids.delete(id);
}

document.getElementById("tr-all").addEventListener("click", toggleBulkHead);
document.getElementById("tr-all-menu").addEventListener("click", openTriageBulkMenu);
