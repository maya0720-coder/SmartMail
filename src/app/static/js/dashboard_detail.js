// 대시보드 상세 패널(디자인 명세 03, 3-5·3-6): 압축 본문(내부 스크롤) + 하단 고정 주요 버튼,
// 1100px 이하에서는 오른쪽 서랍(바깥 클릭·✕·Esc 닫기, 포커스 가두기, 닫으면 선택 행으로 포커스 복귀).
"use strict";

const DRAWER_QUERY = "(max-width: 1100px)";
const isNarrow = () => window.matchMedia(DRAWER_QUERY).matches;

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
  fill(box, ...data.stages.map(stageView),
    data.message ? el("div", { class: "empty" }, data.message) : null,
    el("div", { class: "actions" },
      next ? el("button", { class: "btn sm", onclick: () => runTaskSearch(task, next, box) }, "다음 단계 검색") : null,
      el("button", { class: "btn sm", onclick: async () => { await api("POST", "/api/search/feedback", { task_id: task.id, helpful: true }); toast("평가를 남겼습니다"); } }, "도움 됨"),
      el("button", { class: "btn sm", onclick: async () => { await api("POST", "/api/search/feedback", { task_id: task.id, helpful: false }); toast("평가를 남겼습니다"); } }, "도움 안 됨")));
}

// 메일 원본: 넓은 줌 모달로 보여준다(MAIL-08~10). 원문은 저장하지 않고 열 때마다 원본에서 다시 읽는다.
let mailReturnFocus = null;
async function showMail(refId) {
  mailReturnFocus = document.activeElement;
  const head = document.getElementById("mail-head");
  const body = document.getElementById("mail-body");
  const foot = document.getElementById("mail-foot");
  head.textContent = "원본 메일";
  foot.replaceChildren();
  body.replaceChildren(el("div", { class: "empty" }, "원본을 읽는 중…"));
  openDlg("dlg-mail");
  document.getElementById("mail-close").focus();
  const result = await api("GET", "/api/mail/" + refId);
  if (!result.ok) { body.replaceChildren(el("div", { class: "empty" }, errorText(result, "원본을 읽지 못했습니다"))); return; }
  const m = result.data;
  if (m.missing) { body.replaceChildren(el("div", { class: "empty" }, m.notice)); return; }
  head.textContent = m.subject || "원본 메일";
  const received = (m.received_at || "").slice(0, 16).replace("T", " ");
  fill(body,
    el("div", { class: "mail-meta" },
      el("div", {}, el("span", { class: "k" }, "보낸 사람"), el("span", {}, m.sender_name || "-")),
      el("div", {}, el("span", { class: "k" }, "수신"), el("span", { class: "mono" }, received))),
    el("div", { class: "mail-full" }, m.body || ""),
    m.attachments.length ? el("div", { class: "t-sub mt8" }, "첨부(열지 않음): " + m.attachments.join(", ")) : null);
  fill(foot,
    m.can_open ? el("button", { type: "button", class: "btn", onclick: async () => {
      const r = await api("POST", "/api/mail/" + refId + "/open"); if (!r.ok) toast(errorText(r, "열지 못했습니다"), { kind: "error" });
    } }, "아웃룩에서 열기") : null,
    el("button", { type: "button", class: "btn primary", onclick: closeMailModal }, "닫기"));
}

function closeMailModal() {
  closeDlg("dlg-mail");
  if (mailReturnFocus && document.body.contains(mailReturnFocus)) mailReturnFocus.focus();
}
// 닫기: ✕ 버튼, 바깥(배경) 클릭, Esc
document.getElementById("mail-close").addEventListener("click", closeMailModal);
document.getElementById("dlg-mail").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) closeMailModal();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && document.getElementById("dlg-mail").classList.contains("show")) {
    event.preventDefault();
    closeMailModal();
  }
});

async function cardEditor(task, box) {
  const draft = await api("POST", "/api/history/" + task.id + "/draft");
  if (!draft.ok) { toast(errorText(draft, "초안을 만들지 못했습니다"), { kind: "error" }); return; }
  const field = (label, value) => el("div", { class: "field" }, el("label", {}, label), el("textarea", { maxlength: "2000" }, value || ""));
  const problem = field("문제", draft.data.problem), cause = field("원인", draft.data.cause), action = field("조치", draft.data.action);
  const tcode = el("input", { class: "inline-input", value: draft.data.tcode || "", maxlength: "100", placeholder: "T-code/프로그램" });
  box.replaceChildren(el("div", { class: "sec" }, "히스토리 카드(회신 메일 기반 초안 · 확인 후 저장)"), problem, cause, action, tcode,
    el("div", { class: "actions" }, el("button", { class: "btn sm primary", onclick: async () => {
      const result = await api("POST", "/api/history/" + task.id, {
        problem: problem.querySelector("textarea").value, cause: cause.querySelector("textarea").value,
        action: action.querySelector("textarea").value, tcode: tcode.value || null, tags: task.module_tag ? [task.module_tag] : [],
      });
      toast(result.ok ? "히스토리 카드를 저장했습니다" : errorText(result, "저장하지 못했습니다"), { kind: result.ok ? "ok" : "error" });
    } }, "카드 저장")));
}

// 상태별 하단 고정 버튼: [주요(전체 폭)] + 보조
function footButtons(task, resultBox, onChanged) {
  const act = (action) => () => applyAction(task, action, onChanged);
  const b = (cls, ic, label, onclick) => el("button", { type: "button", class: "btn " + cls, onclick }, ic ? icon(ic) : null, label);
  switch (task.status) {
    case "suggested":
      return [b("primary main", "check", "승인", act("approve")), b("", "x", "무시", act("dismiss"))];
    case "waiting":
    case "on_hold":
      return [b("primary main", "refresh-cw", "진행 시작", act("in_progress")), b("", "check", "완료 확정", act("done")),
        task.status === "waiting" ? b("", null, "보류", act("on_hold")) : null];
    case "in_progress":
    case "done_candidate":
      return [b("primary main", "check", "완료 확정", act("done")), b("", null, "보류", act("on_hold"))];
    case "done":
      return [b("gk main", "book-open", task.gk_link ? "G-Knowledge 재등록" : "G-Knowledge 지식 등록", () => openGkPreview(task.id, onChanged)),
        b("", "file-text", "히스토리 카드", () => cardEditor(task, resultBox))];
    default:
      return [];
  }
}

function renderDetailEmpty() {
  fill(document.getElementById("d-body"), el("div", { class: "d-empty" }, icon("inbox"),
    el("div", {}, "업무를 선택하세요"), el("div", { class: "t-sub" }, "↑↓ 선택 · Enter 열기 · ←→ 페이지")));
  document.getElementById("d-foot").replaceChildren();
}

async function renderDetail(taskId, onChanged) {
  const result = await api("GET", "/api/tasks/" + taskId);
  const body = document.getElementById("d-body");
  const foot = document.getElementById("d-foot");
  if (!result.ok) { body.replaceChildren(el("div", { class: "empty" }, errorText(result, "불러오지 못했습니다"))); foot.replaceChildren(); return; }
  const task = result.data;
  const resultBox = el("div", { class: "d-result" });  // 버튼을 누르면 그때 내용이 생긴다(빈 상자를 미리 두지 않음)
  const refs = task.mail_refs || [];
  const received = refs.length ? (refs[0].received_at || "").slice(5, 16).replace("T", " ") : "-";
  const tools = el("div", { class: "actions tight d-tools" },
    refs.length ? el("button", { type: "button", class: "btn sm", onclick: () => showMail(refs[0].id) }, icon("mail"), "원본 보기") : null,
    el("button", { type: "button", class: "btn sm", onclick: () => runTaskSearch(task, "app", resultBox) }, icon("search"), "유사 이력"),
    el("button", { type: "button", class: "btn sm ai", onclick: () => runTaskSearch(task, "ai", resultBox) }, icon("sparkles"), "AI 지식 요청"));
  fill(body,
    el("div", { class: "d-badges" }, task.module_tag ? el("span", { class: "tag" }, task.module_tag) : null, statusBadge(task)),
    el("h3", { class: "d-title", title: task.title }, task.title),
    el("div", { class: "meta" },
      el("div", {}, "요청 부서"), el("div", {}, task.requester_dept || "-"),
      el("div", {}, "기한"), el("div", {}, dueText(task)),
      el("div", {}, "수신"), el("div", { class: "mono" }, received),
      el("div", { class: "ev" }, "근거"), el("div", { class: "ev" }, task.evidence ? "“" + task.evidence + "”" : "-"),
      el("div", {}, "지식 등록"), el("div", {}, task.gk_link ? el("a", { class: "link", href: task.gk_link, target: "_blank", rel: "noopener noreferrer" }, "등록됨") : "미등록")),
    tools,
    refs.length > 1 ? el("details", { class: "d-refs" }, el("summary", {}, "원본 " + refs.length + "건"),
      ...refs.slice(1).map((ref) => el("button", { type: "button", class: "btn sm", onclick: () => showMail(ref.id) },
        icon("mail"), (ref.received_at || "").slice(5, 16).replace("T", " ")))) : null,
    resultBox);
  fill(foot, el("div", { class: "d-btns" }, ...footButtons(task, resultBox, onChanged)),
    task.retention_days_left != null ? el("div", { class: "ret" }, "보관 D-" + task.retention_days_left + " · 기간이 지나면 앱에서 삭제" + (task.gk_link ? "" : " (G-Knowledge 미등록)")) : null);
}

// ---- 서랍(1100px 이하) ----
let drawerReturnFocus = null;

function drawerOpen() { return document.body.classList.contains("det-open"); }

function openDrawer(returnTo) {
  if (!isNarrow()) return;
  const panel = document.getElementById("detail");
  drawerReturnFocus = returnTo || null;
  document.body.classList.add("det-open");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  document.getElementById("d-close").focus();
}

function closeDrawer() {
  if (!drawerOpen()) return false;
  const panel = document.getElementById("detail");
  document.body.classList.remove("det-open");
  panel.removeAttribute("role");
  panel.removeAttribute("aria-modal");
  // 행은 선택할 때 다시 그려지므로, 저장한 요소가 사라졌으면 현재 선택 행으로 돌려준다
  const back = drawerReturnFocus && document.body.contains(drawerReturnFocus)
    ? drawerReturnFocus : document.querySelector("#todo tr.row.sel");
  if (back) back.focus();
  return true;
}

// 서랍이 열려 있으면 Tab이 서랍 안에서만 돈다
document.getElementById("detail").addEventListener("keydown", (event) => {
  if (event.key !== "Tab" || !drawerOpen()) return;
  const items = [...event.currentTarget.querySelectorAll("button, a[href], input, textarea, select, summary, [tabindex]:not([tabindex='-1'])")]
    .filter((node) => !node.disabled && node.offsetParent !== null);
  if (!items.length) return;
  const first = items[0], last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});
document.getElementById("d-close").addEventListener("click", closeDrawer);
document.getElementById("scrim").addEventListener("click", closeDrawer);
// 넓은 화면으로 바뀌면 서랍 상태를 정리한다
window.matchMedia(DRAWER_QUERY).addEventListener("change", (event) => { if (!event.matches) closeDrawer(); });
