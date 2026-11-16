// 제안 일괄 처리(U2, TODO-02, 결정기록 0036).
// - 선택: Ctrl/⌘+클릭 토글 · Shift+클릭 범위(현재 페이지 안) · Ctrl+A 현재 페이지 전체 · 체크박스
//   키보드: Space 토글 · Shift+↑↓ 범위 넓히기 · Esc 선택 해제
// - 2건 이상이면 상세 대신 일괄 작업 바: 할 일로 A · 나중에 S · 업무 아님 D · 구분 일괄 변경 · 이 탭 전체 선택
// 선택 상태는 메모리에만 둔다. 모든 글자는 el()/textContent로만 넣는다.
"use strict";

const BULK = { ids: new Set(), anchor: null };
const BULK_MAX = 100;  // 서버 한도와 같음
const BULK_UNDO_MS = 10000;
const BULK_DONE = {
  approve: "할 일로 옮겼습니다", snooze: "나중에로 미뤘습니다",
  dismiss: "업무 아님으로 정리했습니다", category: "구분을 바꿨습니다",
};

function bulkActive() { return BULK.ids.size >= 2; }

// 목록 행의 선택 표시만 바꾼다(목록을 다시 그리지 않음)
function syncBulkMarks() {
  document.getElementById("tr-items").classList.toggle("multi", BULK.ids.size > 0);
  for (const li of document.querySelectorAll("#tr-items .tr-item")) {
    const on = BULK.ids.has(Number(li.dataset.id));
    li.classList.toggle("picked", on);
    const box = li.querySelector(".tr-check");
    if (box) box.checked = on;
  }
}

// 선택이 바뀐 뒤: 2건 이상이면 일괄 바, 아니면 지금 고른 항목의 상세
function bulkChanged() {
  syncBulkMarks();
  if (bulkActive()) { renderBulkBar(); return; }
  const task = findTask(TR.selectedId);
  if (task && (!detailTask || detailTask.id !== task.id || document.getElementById("bulk-bar"))) renderTriageDetail(task);
}

function clearBulk() {
  if (!BULK.ids.size) return false;
  BULK.ids.clear();
  BULK.anchor = null;
  bulkChanged();
  return true;
}

// 처음 다중 선택을 시작하면 지금 보고 있는 항목도 선택에 넣는다
function seedBulk() {
  if (!BULK.ids.size && TR.selectedId !== null && findTask(TR.selectedId)) BULK.ids.add(TR.selectedId);
}

function toggleBulk(id) {
  if (id !== TR.selectedId) seedBulk();
  if (BULK.ids.has(id)) BULK.ids.delete(id);
  else if (BULK.ids.size < BULK_MAX) BULK.ids.add(id);
  else toast("한 번에 " + BULK_MAX + "건까지 고를 수 있습니다", { kind: "warn" });
  BULK.anchor = id;
  bulkChanged();
}

// Shift 범위 선택: 현재 페이지에 보이는 항목 안에서만(페이지 경계를 넘지 않음)
function rangeBulk(id) {
  const order = visibleOrder().map((t) => t.id);
  const from = order.indexOf(BULK.anchor !== null ? BULK.anchor : TR.selectedId);
  const to = order.indexOf(id);
  if (to < 0) return;
  const [lo, hi] = from < 0 ? [to, to] : [Math.min(from, to), Math.max(from, to)];
  BULK.ids = new Set(order.slice(lo, hi + 1).slice(0, BULK_MAX));
  bulkChanged();
}

function selectBulk(ids) {
  BULK.ids = new Set(ids.slice(0, BULK_MAX));
  BULK.anchor = null;
  if (ids.length > BULK_MAX) toast("앞의 " + BULK_MAX + "건만 골랐습니다", { kind: "warn" });
  bulkChanged();
}
function selectBulkPage() { selectBulk(visibleOrder().map((t) => t.id)); }
function selectBulkTab() { selectBulk(filteredItems().map((t) => t.id)); }

// 목록 행 클릭(triage_list.js에서 부름). 처리했으면 true
function bulkClick(event, id) {
  if (event.shiftKey) { event.preventDefault(); rangeBulk(id); return true; }
  if (event.ctrlKey || event.metaKey) { event.preventDefault(); toggleBulk(id); return true; }
  BULK.ids.clear();
  BULK.anchor = id;
  syncBulkMarks();
  return false;
}

// 키보드 다중 선택. Shift+↑↓는 지금 항목을 넣고 옮긴 다음 항목도 넣는다(현재 페이지 안)
function extendBulk(step) {
  const order = visibleOrder().map((t) => t.id);
  const index = order.indexOf(TR.selectedId);
  const next = order[index + step];
  if (index < 0 || next === undefined) return;
  seedBulk();
  if (BULK.ids.size < BULK_MAX) BULK.ids.add(next);
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
  if (!bulkActive()) return false;
  const actions = { a: "approve", s: "snooze", d: "dismiss" };
  if (actions[key]) { bulkDecide(actions[key]); return true; }
  return false;
}

function renderBulkBar() {
  const n = BULK.ids.size;
  const tabCount = filteredItems().length;
  const cat = el("select", { class: "inline-input", id: "bulk-cat", "aria-label": "바꿀 구분" },
    ...TR.categories.map((c) => el("option", { value: c }, c)));
  fill(document.getElementById("tr-detail"),
    el("div", { class: "bulk-bar", id: "bulk-bar", role: "region", "aria-label": "일괄 작업" },
      el("div", { class: "bulk-head" }, icon("check"), el("b", { id: "bulk-count" }, "선택 " + n + "건"),
        el("span", { class: "t-sub" }, "(한 번에 최대 " + BULK_MAX + "건)")),
      el("div", { class: "tr-actions bulk-actions" },
        el("button", { type: "button", class: "btn primary", id: "bulk-approve", onclick: () => bulkDecide("approve") }, icon("check"), "할 일로", el("kbd", {}, "A")),
        el("button", { type: "button", class: "btn", id: "bulk-snooze", onclick: () => bulkDecide("snooze") }, icon("calendar-range"), "나중에", el("kbd", {}, "S")),
        el("button", { type: "button", class: "btn ghost", id: "bulk-dismiss", onclick: () => bulkDecide("dismiss") }, "업무 아님", el("kbd", {}, "D")),
        el("button", { type: "button", class: "btn ghost", id: "bulk-clear", onclick: clearBulk }, "선택 해제", el("kbd", {}, "Esc"))),
      el("div", { class: "bulk-row" }, el("span", { class: "fl" }, "구분 일괄 변경"), cat,
        el("button", { type: "button", class: "btn sm", id: "bulk-cat-apply", disabled: !TR.categories.length,
          onclick: () => bulkDecide("category", cat.value) }, "적용")),
      el("div", { class: "bulk-row" },
        el("button", { type: "button", class: "btn sm", id: "bulk-tab-all", disabled: n >= Math.min(tabCount, BULK_MAX),
          onclick: selectBulkTab }, "이 탭 전체 선택 (" + Math.min(tabCount, BULK_MAX) + "건)"),
        el("span", { class: "t-sub" }, "Ctrl+A 이 페이지 · Shift+클릭 범위 · Ctrl+클릭 하나씩"))));
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
  BULK.ids = new Set(result.data.failed.map((f) => f.id));  // 실패한 건은 선택으로 남긴다
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

// 목록을 다시 불러온 뒤 사라진 항목은 선택에서 뺀다(triage_list.js loadTriage에서 부름)
function pruneBulk() {
  for (const id of [...BULK.ids]) if (!findTask(id)) BULK.ids.delete(id);
}
