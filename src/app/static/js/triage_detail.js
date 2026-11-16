// 제안된 업무 트리아지 — 상세(결정기록 0015, 명세 2-1).
// 근거 메일은 볼 때마다 서버가 원본을 다시 읽는다(저장 없음). 바꾼 값은 '할 일로' 때 함께 저장한다.
"use strict";

let pendingEdit = {};  // 선택한 업무의 수정값(메모리에만)
let detailTask = null;
const RESULT_LABEL = { approve: "할 일로 보냄", snooze: "나중에(다음 근무일 아침)", dismiss: "업무 아님" };
const UNDO_MS = 60000;  // 되돌리기 가능 시간(서버 토큰 유효 시간과 같음)
// 주간보고 예상 위치(WIKI-01). 빈 값은 자동 분류
const WEEKLY_LABELS = { "": "자동", done: "금주 실적", planned: "차주 계획", issues: "이슈·요청", none: "넣지 않음" };
const TR_DRAWER_QUERY = "(max-width: 900px)";

function renderTriageEmpty() {
  detailTask = null;
  document.getElementById("tr-detail").replaceChildren(el("div", { class: "d-empty" }, icon("inbox"),
    el("div", {}, "제안을 고르세요"), el("div", { class: "t-sub" }, "↑↓ 이동 · A 할 일로 · S 나중에 · D 업무 아님")));
}

// 읽기 모드 칸: 누르면 편집기로 바뀐다
function fieldCell(label, shown, makeEditor) {
  const value = el("button", { type: "button", class: "fv" }, shown || "-");
  const cell = el("div", { class: "fcell" }, el("span", { class: "fl" }, label), value);
  value.addEventListener("click", () => {
    const editor = makeEditor();
    value.replaceWith(editor);
    editor.focus();
  });
  return cell;
}

function titleNode(task) {
  const text = pendingEdit.title || task.title;
  return el("div", { class: "tr-dtitle", id: "tr-dtitle" },
    summaryChip(task.summary_state),
    el("h3", { title: text }, text),
    el("button", { type: "button", class: "icon-btn sm", "aria-label": "제목 고치기 (E)", title: "제목 고치기 (E)", onclick: startTitleEdit }, icon("pen-line")));
}

// 요약이 끝나 제목·상태가 바뀌면 상세 제목도 바꾼다(편집 중이면 건드리지 않음)
function refreshDetailTitle(task) {
  const box = document.getElementById("tr-dtitle");
  if (!detailTask || detailTask.id !== task.id || !box || box.querySelector("input")) return;
  detailTask.title = task.title;
  detailTask.summary_state = task.summary_state;
  box.replaceWith(titleNode(detailTask));
}

function startTitleEdit() {
  if (!detailTask) return;
  const box = document.getElementById("tr-dtitle");
  const input = el("input", { class: "inline-input", value: pendingEdit.title || detailTask.title, maxlength: "200", "aria-label": "업무 제목" });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      const v = input.value.trim();
      if (v && v !== detailTask.title) pendingEdit.title = v; else delete pendingEdit.title;
      box.replaceWith(titleNode(detailTask));
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      box.replaceWith(titleNode(detailTask));
    }
  });
  box.replaceChildren(input);
  input.focus();
  input.select();
}

function contextBody(data) {
  if (data.missing) return el("div", { class: "empty" }, "원문 없음(메일이 삭제·이동되었습니다)");
  const hl = data.highlight;
  const lines = data.lines.map((line, i) => el("p", {},
    hl && hl.line === i ? markText(line, hl.start, hl.end) : line));
  return el("div", { class: "tr-mail" }, ...lines,
    data.attachments.length ? el("div", { class: "t-sub" }, "첨부(열지 않음): " + data.attachments.join(", ")) : null);
}

// 요청 문장 하이라이트가 처음부터 보이게 메일 영역 안에서만 스크롤한다(결정기록 0033, 바깥 패널은 움직이지 않음)
function revealHighlight(container) {
  const box = container.querySelector(".tr-mail");
  const mark = box && box.querySelector("mark");
  if (!mark) return;
  box.scrollTop = Math.max(0, mark.offsetTop - Math.round(box.clientHeight / 3));
}

// 원본 메일 크게 보기(모달). 화면에 이미 읽어 온 내용을 복제해 보여 준다(서버 재요청·저장 없음)
function openMailFull(source) {
  const box = source.querySelector(".tr-mail");
  if (!box) return;
  const copy = box.cloneNode(true);
  copy.classList.add("full");
  document.getElementById("mail-full").replaceChildren(copy);
  openDlg("dlg-mail");
  revealHighlight(document.getElementById("mail-full"));
  document.getElementById("mail-full-close").focus();
}
function closeMailFull() {
  closeDlg("dlg-mail");
  document.getElementById("mail-full").replaceChildren();
  const back = document.getElementById("tr-mail-full");
  if (back) back.focus();
}

// ---- 상세 넓게 보기(결정기록 0033): 목록을 아이콘 열로 접고 상세가 화면 대부분을 쓴다. 상태는 서버 설정에 저장 ----
function triageWide() { return document.body.classList.contains("tr-wide"); }
function syncWideButtons() {
  const on = triageWide();
  for (const button of document.querySelectorAll("[data-wide-toggle]")) {
    button.setAttribute("aria-pressed", String(on));
    const label = button.querySelector(".wide-label");
    if (label) label.textContent = on ? "목록 보기" : "넓게 보기";
  }
}
function toggleTriageWide() {
  if (window.matchMedia(TR_DRAWER_QUERY).matches) return;  // 서랍 구간에서는 목록과 상세가 겹치지 않는다
  const on = !triageWide();
  document.body.classList.toggle("tr-wide", on);
  syncWideButtons();
  api("PUT", "/api/ui-prefs", { triage_wide: on });
  if (!on) {
    const sel = document.querySelector("#tr-items .tr-item.sel");
    if (sel) sel.scrollIntoView({ block: "nearest" });
  }
}
function wideButton() {
  const on = triageWide();
  return el("button", { type: "button", class: "btn sm tr-wide-btn", "data-wide-toggle": "1", "aria-pressed": String(on),
    title: "상세 넓게 보기 (W)", onclick: toggleTriageWide }, el("span", { class: "wide-label" }, on ? "목록 보기" : "넓게 보기"), el("kbd", {}, "W"));
}
if (document.body.dataset.trWide === "1" && !window.matchMedia(TR_DRAWER_QUERY).matches) document.body.classList.add("tr-wide");
document.getElementById("tr-rail").addEventListener("click", toggleTriageWide);
document.getElementById("mail-full-close").addEventListener("click", closeMailFull);
document.getElementById("dlg-mail").addEventListener("keydown", (event) => {
  if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeMailFull(); }
});
document.getElementById("dlg-mail").addEventListener("click", (event) => { if (event.target.id === "dlg-mail") closeMailFull(); });

async function renderTriageDetail(task) {
  detailTask = task;
  pendingEdit = {};
  const box = document.getElementById("tr-detail");
  const subject = el("div", { class: "tr-orig" }, icon("mail"), el("span", {}, "원래 메일 제목: 불러오는 중…"));
  const mail = el("div", { class: "tr-sec-body" }, el("div", { class: "empty" }, "메일을 읽는 중…"));
  const similar = el("div", { class: "tr-sec-body" }, el("div", { class: "t-sub" }, "찾는 중…"));
  const similarCount = el("span", { class: "t-sub" }, "찾는 중");
  const catSelect = () => {
    const s = el("select", { class: "inline-input", "aria-label": "구분" },
      ...TR.categories.map((n) => el("option", { value: n, selected: n === (pendingEdit.module_tag || task.module_tag) }, n)));
    s.addEventListener("change", () => { if (s.value !== task.module_tag) pendingEdit.module_tag = s.value; else delete pendingEdit.module_tag; });
    return s;
  };
  const dueInput = () => {
    const i = el("input", { class: "inline-input", type: "date", value: pendingEdit.due_date || (task.due_at || "").slice(0, 10), "aria-label": "기한" });
    i.addEventListener("change", () => { if (i.value) pendingEdit.due_date = i.value; else delete pendingEdit.due_date; });
    return i;
  };
  const deptInput = () => {
    const i = el("input", { class: "inline-input", value: pendingEdit.requester_dept || task.requester_dept || "", maxlength: "100", "aria-label": "요청 부서" });
    i.addEventListener("change", () => { const v = i.value.trim(); if (v !== (task.requester_dept || "")) pendingEdit.requester_dept = v; else delete pendingEdit.requester_dept; });
    return i;
  };
  const weeklySelect = () => {
    const now = pendingEdit.weekly_section || task.weekly_section || "";
    const s = el("select", { class: "inline-input", "aria-label": "주간보고 예상 위치" },
      ...Object.entries(WEEKLY_LABELS).map(([v, n]) => el("option", { value: v, selected: v === now }, n)));
    s.addEventListener("change", () => { if (s.value) pendingEdit.weekly_section = s.value; else delete pendingEdit.weekly_section; });
    return s;
  };
  fill(box,
    el("button", { type: "button", class: "d-close tr-close", id: "tr-close", "aria-label": "상세 닫기", onclick: closeTriageDrawer }, icon("x")),
    el("div", { class: "tr-dhead" }, titleNode(task), confidenceBar(task.confidence), wideButton()),
    subject,
    el("div", { class: "tr-fields" },
      fieldCell("구분", task.module_tag, catSelect),
      fieldCell("기한", task.due_at ? task.due_at.slice(0, 10) : "미정", dueInput),
      fieldCell("요청 부서", task.requester_label || task.requester_dept, deptInput),
      fieldCell("주간보고 예상 위치", WEEKLY_LABELS[task.weekly_section || ""], weeklySelect)),
    el("div", { class: "tr-sec" }, el("div", { class: "tr-sec-h" }, icon("sparkles"), "왜 제안됐나요"),
      el("div", { class: "tr-reasons" }, ...(task.reasons.length ? task.reasons : ["none"]).map((code) =>
        el("span", { class: "tchip reason" }, REASON_LABELS[code] || "규칙 판별")))),
    el("div", { class: "tr-sec tr-sec-mail" }, el("div", { class: "tr-sec-h" }, icon("mail"), "메일에서 찾은 요청 문장",
      el("button", { type: "button", class: "btn sm tr-mail-full", id: "tr-mail-full", disabled: true, onclick: () => openMailFull(mail) }, "크게 보기")), mail),
    // 비슷한 지난 업무는 접어 두고 건수만 보인다(메일 영역에 높이를 양보, 결정기록 0033)
    el("details", { class: "tr-sec tr-sec-past" }, el("summary", { class: "tr-sec-h" }, icon("history"), "비슷한 지난 업무 ", similarCount), similar),
    el("div", { class: "tr-actions" },
      el("button", { type: "button", class: "btn primary", id: "tr-approve", onclick: () => decideTriage("approve") }, icon("check"), "할 일로", el("kbd", {}, "A")),
      el("button", { type: "button", class: "btn", id: "tr-snooze", onclick: () => decideTriage("snooze") }, icon("calendar-range"), "나중에", el("kbd", {}, "S")),
      el("button", { type: "button", class: "btn ghost", id: "tr-dismiss", onclick: () => decideTriage("dismiss") }, "업무 아님", el("kbd", {}, "D")),
      // 단축키 안내: 제목 줄에서 옮김(결정기록 0016)
      el("span", { class: "tr-keys", "aria-hidden": "true" }, el("kbd", {}, "↑"), el("kbd", {}, "↓"), " 이동 · ", el("kbd", {}, "E"), " 제목 고치기")));
  const [ctx, past] = await Promise.all([
    api("GET", "/api/tasks/" + task.id + "/context"),
    api("GET", "/api/history?days=7&q=" + encodeURIComponent(task.title.slice(0, 40))),
  ]);
  if (detailTask !== task) return;  // 그 사이 다른 항목을 골랐으면 그리지 않는다
  if (ctx.ok) {
    subject.replaceChildren(icon("mail"), el("span", {}, ctx.data.missing ? "원래 메일 제목: 원문 없음" : "원래 메일 제목: " + ctx.data.subject));
    mail.replaceChildren(contextBody(ctx.data));
    revealHighlight(mail);
    document.getElementById("tr-mail-full").disabled = Boolean(ctx.data.missing);
  } else {
    subject.replaceChildren(icon("mail"), el("span", {}, "원래 메일 제목: 읽지 못함"));
    mail.replaceChildren(el("div", { class: "empty" }, errorText(ctx, "메일을 읽지 못했습니다")));
  }
  const hits = past.ok ? past.data.filter((h) => h.task_id !== task.id).slice(0, 2) : [];
  similarCount.textContent = "(" + hits.length + "건)";
  similar.replaceChildren(...(hits.length ? hits.map((h) => el("div", { class: "tr-past" },
    el("b", {}, h.title), el("span", { class: "t-sub" }, " — " + h.action))) : [el("div", { class: "t-sub" }, "최근 7일 안에 비슷한 업무가 없습니다")]));
}

async function decideTriage(action) {
  const task = detailTask;
  if (!task) return;
  let result;
  if (action === "approve") {
    const edit = { ...pendingEdit };
    result = await api("POST", "/api/tasks/" + task.id + "/approve", Object.keys(edit).length ? edit : undefined);
  } else {
    result = await api("POST", "/api/tasks/" + task.id + "/" + action);
  }
  if (!result.ok) { toast(errorText(result, "처리하지 못했습니다"), { kind: "error" }); return; }
  const token = result.data.decision_token;
  const title = pendingEdit.title || task.title;
  if (action === "snooze") TR.summary.snoozed += 1;  // 머리의 '나중에 n건'을 바로 반영
  closeTriageDrawer();
  removeTriageItem(task.id);
  toast(title + " · " + RESULT_LABEL[action], token ? {
    action: "되돌리기", ms: UNDO_MS,
    onAction: () => revertTriage(task.id, token),
  } : {});
}

async function revertTriage(taskId, token) {
  const result = await api("POST", "/api/tasks/" + taskId + "/revert", { decision_token: token });
  if (!result.ok) { toast(errorText(result, "되돌리지 못했습니다"), { kind: "warn" }); return; }
  toast("되돌렸습니다", { kind: "ok" });
  await loadTriage(taskId);
}

// ---- 좁은 화면(≤900px): 상세를 오른쪽 서랍으로 연다 ----
let trReturnFocus = null;

function triageDrawerOpen() { return document.body.classList.contains("tr-open"); }

function openTriageDrawer(returnTo) {
  if (!window.matchMedia(TR_DRAWER_QUERY).matches || !detailTask) return;
  const panel = document.getElementById("tr-detail");
  trReturnFocus = returnTo || null;
  document.body.classList.add("tr-open");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  const close = document.getElementById("tr-close");
  if (close) close.focus();
}

function closeTriageDrawer() {
  if (!triageDrawerOpen()) return false;
  const panel = document.getElementById("tr-detail");
  document.body.classList.remove("tr-open");
  panel.removeAttribute("role");
  panel.removeAttribute("aria-modal");
  const back = trReturnFocus && document.body.contains(trReturnFocus)
    ? trReturnFocus : document.querySelector("#tr-items .tr-item.sel");
  if (back) back.focus();
  return true;
}

// 서랍이 열려 있으면 Tab이 서랍 안에서만 돈다. 입력칸 안의 Esc도 서랍을 닫는다
document.getElementById("tr-detail").addEventListener("keydown", (event) => {
  if (!triageDrawerOpen()) return;
  if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeTriageDrawer(); return; }
  if (event.key !== "Tab") return;
  const items = [...event.currentTarget.querySelectorAll("button, a[href], input, textarea, select, [tabindex]:not([tabindex='-1'])")]
    .filter((node) => !node.disabled && node.offsetParent !== null);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});
document.getElementById("tr-scrim").addEventListener("click", closeTriageDrawer);
window.matchMedia(TR_DRAWER_QUERY).addEventListener("change", (event) => { if (!event.matches) closeTriageDrawer(); });
