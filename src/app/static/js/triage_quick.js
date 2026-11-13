// 제안된 업무 — 빠른 정리 모드(B안, 결정기록 0015, 명세 2-2).
// 현재 탭 목록을 카드 대기열로 쓴다. → 할 일로 · ← 업무 아님 · ↓ 나중에. 끌기(110px)에는 항상 같은 버튼 대안이 있다.
// 빠른 모드에서는 편집하지 않는다(E → A안 상세로 열고 종료). 모든 글자는 el()/textContent로만 넣는다.
"use strict";

const QUICK = { active: false, queue: [], index: 0, tally: { approve: 0, snooze: 0, dismiss: 0 }, busy: false };
const QUICK_DRAG = 110;  // 결정으로 보는 끌기 거리(px)
const QUICK_FLY_MS = 220;
const QUICK_LABEL = { approve: "할 일", snooze: "나중에", dismiss: "업무 아님" };
const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function startQuickMode() {
  if (QUICK.active) return;
  closeTriageDrawer();
  QUICK.active = true;
  QUICK.queue = filteredItems().slice();
  QUICK.index = 0;
  QUICK.tally = { approve: 0, snooze: 0, dismiss: 0 };
  document.body.classList.add("quick-on");
  document.getElementById("tr-quick").classList.remove("hidden");
  const button = document.getElementById("tr-quick-btn");
  button.textContent = "목록으로 돌아가기";
  button.setAttribute("aria-pressed", "true");
  renderQuick();
}

function endQuickMode(openId) {
  if (!QUICK.active) return;
  QUICK.active = false;
  document.body.classList.remove("quick-on");
  document.getElementById("tr-quick").classList.add("hidden");
  const button = document.getElementById("tr-quick-btn");
  button.replaceChildren("⚡ 빠른 정리 ", el("kbd", {}, "Q"));
  button.setAttribute("aria-pressed", "false");
  loadTriage(openId).then(() => {
    if (openId !== undefined) openTriageDrawer(document.querySelector("#tr-items .tr-item.sel"));
  });
}

function renderTally() {
  const t = QUICK.tally;
  fill(document.getElementById("q-tally"),
    el("div", { class: "q-tally-h" }, "이번 정리"),
    ...["approve", "snooze", "dismiss"].map((k) => el("div", { class: "q-t " + k }, el("span", {}, QUICK_LABEL[k]), el("b", {}, String(t[k])))));
  const next = QUICK.queue.slice(QUICK.index + 1, QUICK.index + 6);
  fill(document.getElementById("q-queue"), ...(next.length ? next.map((task) => el("li", { title: task.title }, task.title))
    : [el("li", { class: "t-sub" }, "남은 카드 없음")]));
}

function quickCard(task) {
  const position = (QUICK.index + 1) + " / " + QUICK.queue.length;
  const body = el("div", { class: "q-body" }, el("div", { class: "t-sub" }, "메일을 읽는 중…"));
  const card = el("article", { class: "q-card", id: "q-card", tabindex: "-1", "aria-label": "제안 " + position + ": " + task.title },
    el("div", { class: "q-top" }, el("span", { class: "q-pos" }, position), el("span", { class: "tm" }, receivedLabel(task.requested_at)), confidenceBar(task.confidence)),
    el("h3", { class: "q-title" }, task.title),
    el("div", { class: "tr-line2" }, categoryChip(task), dueChip(task), task.requester_dept ? el("span", { class: "dept" }, task.requester_dept) : null),
    body,
    el("div", { class: "tr-reasons" }, ...task.reasons.map((code) => el("span", { class: "tchip reason" }, REASON_LABELS[code] || "규칙 판별"))),
    el("div", { class: "q-hint q-hint-l", "aria-hidden": "true" }, "업무 아님"),
    el("div", { class: "q-hint q-hint-r", "aria-hidden": "true" }, "할 일로"),
    el("div", { class: "q-hint q-hint-d", "aria-hidden": "true" }, "나중에"));
  api("GET", "/api/tasks/" + task.id + "/context").then((ctx) => {
    if (!document.body.contains(card)) return;
    body.replaceChildren(ctx.ok ? contextBody(ctx.data) : el("div", { class: "empty" }, errorText(ctx, "메일을 읽지 못했습니다")));
  });
  enableDrag(card);
  return card;
}

function renderQuick() {
  renderTally();
  const main = document.getElementById("q-main");
  const task = QUICK.queue[QUICK.index];
  if (!task) {
    const t = QUICK.tally;
    fill(main, el("div", { class: "card q-done" }, icon("check"),
      el("h3", {}, "모두 정리했어요"),
      el("div", { class: "q-done-sum", id: "q-done-sum" }, "할 일 " + t.approve + " · 나중에 " + t.snooze + " · 업무 아님 " + t.dismiss),
      el("div", { class: "q-done-btns" },
        // el()은 https 링크만 허용하므로 고정된 같은 출처 경로로 이동하는 버튼을 쓴다
        el("button", { type: "button", class: "btn primary", id: "q-home", onclick: () => { window.location.assign("/"); } }, "대시보드로"),
        el("button", { type: "button", class: "btn", onclick: () => endQuickMode() }, "목록으로"))));
    return;
  }
  const stack = el("div", { class: "q-stack" },
    QUICK.queue.length - QUICK.index > 2 ? el("div", { class: "q-back b2", "aria-hidden": "true" }) : null,
    QUICK.queue.length - QUICK.index > 1 ? el("div", { class: "q-back b1", "aria-hidden": "true" }) : null,
    quickCard(task));
  fill(main, stack,
    el("div", { class: "q-btns" },
      el("button", { type: "button", class: "btn ghost", id: "q-dismiss", onclick: () => quickDecide("dismiss") }, "← 업무 아님"),
      el("button", { type: "button", class: "btn", id: "q-snooze", onclick: () => quickDecide("snooze") }, "↓ 나중에"),
      el("button", { type: "button", class: "btn primary", id: "q-approve", onclick: () => quickDecide("approve") }, "할 일로 →")),
    el("div", { class: "q-keys t-sub" }, "← → ↓ 결정 · E 상세에서 고치기 · Esc 끝내기"));
  document.getElementById("q-card").focus({ preventScroll: true });
}

async function quickDecide(action) {
  const task = QUICK.queue[QUICK.index];
  if (!task || QUICK.busy) return;
  QUICK.busy = true;
  const card = document.getElementById("q-card");
  const result = await api("POST", "/api/tasks/" + task.id + "/" + action);
  if (!result.ok) {
    QUICK.busy = false;
    if (card) card.style.transform = "";
    toast(errorText(result, "처리하지 못했습니다"), { kind: "error" });
    return;
  }
  QUICK.tally[action] += 1;
  TR.summary.visible = Math.max(0, TR.summary.visible - 1);  // 머리의 건수·진행 막대도 바로 반영
  TR.summary.decided_today += 1;
  if (action === "snooze") TR.summary.snoozed += 1;
  renderCount();
  const token = result.data.decision_token;
  toast(task.title + " · " + RESULT_LABEL[action], token ? { action: "되돌리기", ms: UNDO_MS, onAction: () => quickUndo(task, action, token) } : {});
  const advance = () => { QUICK.index += 1; QUICK.busy = false; renderQuick(); };
  if (card && !reduceMotion()) {
    card.style.transform = "";
    card.classList.add("fly-" + action);
    setTimeout(advance, QUICK_FLY_MS);
  } else advance();
}

async function quickUndo(task, action, token) {
  const result = await api("POST", "/api/tasks/" + task.id + "/revert", { decision_token: token });
  if (!result.ok) { toast(errorText(result, "되돌리지 못했습니다"), { kind: "warn" }); return; }
  toast("되돌렸습니다", { kind: "ok" });
  if (!QUICK.active) { loadTriage(task.id); return; }
  QUICK.tally[action] = Math.max(0, QUICK.tally[action] - 1);
  TR.summary.visible += 1;
  TR.summary.decided_today = Math.max(0, TR.summary.decided_today - 1);
  if (action === "snooze") TR.summary.snoozed = Math.max(0, TR.summary.snoozed - 1);
  renderCount();
  QUICK.queue.splice(QUICK.index, 0, task);  // 되돌린 카드를 바로 다시 보여 준다
  renderQuick();
}

// 마우스·터치 끌기: 가로 110px(→ 할 일로 / ← 업무 아님) 또는 아래로 110px(나중에)를 넘으면 결정
function enableDrag(card) {
  let start = null;
  card.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest("a, button")) return;
    start = { x: event.clientX, y: event.clientY, id: event.pointerId };
    card.setPointerCapture(event.pointerId);
    card.classList.add("dragging");
  });
  card.addEventListener("pointermove", (event) => {
    if (!start || event.pointerId !== start.id) return;
    const dx = event.clientX - start.x, dy = Math.max(0, event.clientY - start.y);
    card.style.transform = "translate(" + dx + "px," + dy + "px) rotate(" + (dx / 20) + "deg)";
    card.dataset.hint = dx > QUICK_DRAG ? "approve" : dx < -QUICK_DRAG ? "dismiss" : dy > QUICK_DRAG ? "snooze" : "";
  });
  const finish = (event) => {
    if (!start || event.pointerId !== start.id) return;
    const dx = event.clientX - start.x, dy = event.clientY - start.y;
    start = null;
    card.classList.remove("dragging");
    card.dataset.hint = "";
    if (dx > QUICK_DRAG) quickDecide("approve");
    else if (dx < -QUICK_DRAG) quickDecide("dismiss");
    else if (dy > QUICK_DRAG) quickDecide("snooze");
    else card.style.transform = "";
  };
  card.addEventListener("pointerup", finish);
  card.addEventListener("pointercancel", () => { start = null; card.classList.remove("dragging"); card.dataset.hint = ""; card.style.transform = ""; });
}

document.getElementById("tr-quick-btn").addEventListener("click", () => { if (QUICK.active) endQuickMode(); else startQuickMode(); });

document.addEventListener("keydown", (event) => {
  // defaultPrevented: 같은 키로 목록 쪽이 방금 모드를 켰으면 여기서 다시 끄지 않는다
  if (!QUICK.active || event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
  if (document.querySelector(".dim.show")) return;
  const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  const task = QUICK.queue[QUICK.index];
  const actions = {
    ArrowRight: () => quickDecide("approve"),
    ArrowLeft: () => quickDecide("dismiss"),
    ArrowDown: () => quickDecide("snooze"),
    e: () => { if (task) endQuickMode(task.id); },
    Escape: () => endQuickMode(),
    q: () => endQuickMode(),
  };
  if (actions[key]) { event.preventDefault(); actions[key](); }
});
