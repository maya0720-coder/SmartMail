// 대시보드 상세 패널(디자인 명세 03, 3-5·3-6): 압축 본문(내부 스크롤) + 하단 고정 주요 버튼,
// 1100px 이하에서는 오른쪽 서랍(바깥 클릭·✕·Esc 닫기, 포커스 가두기, 닫으면 선택 행으로 포커스 복귀).
"use strict";

const DRAWER_QUERY = "(max-width: 1100px)";
const isNarrow = () => window.matchMedia(DRAWER_QUERY).matches;

// 상태 배지·업무 구분 배지는 task_badges.js, 원본 메일 영역은 detail_mail.js

function dueText(task) {
  if (!task.due_at) return el("span", {}, "미정");
  const date = task.due_at.slice(5, 10).replace("-", "/");
  const cls = task.due_badge === "overdue" ? "due-crit" : task.due_badge === "soon" ? "due-warn" : "";
  return el("span", { class: cls }, date + (task.due_badge === "overdue" ? " 지연" : ""));
}

// 단계별 결과(결정기록 0021): 앱 이력·G-Knowledge 사례를 보여 주고, AI 해결 방안에는 근거 사례 수를 붙인다
function stageView(stage) {
  const cls = stage.stage === "app" ? "app" : stage.stage === "gk" ? "gk" : "ai";
  const head = el("span", { class: "src " + cls }, stage.badge);
  if (!stage.ran) return null;
  if (!stage.found) return el("div", { class: "step miss" }, head, stage.notice || "찾지 못함");
  if (stage.ai_answer) {
    const basis = stage.cases_used ? "위 사례 " + stage.cases_used + "건을 근거로 제안" : "비슷한 사례 없이 업무 내용만으로 제안";
    return el("div", { class: "step aihit" }, head, el("span", { class: "t-sub" }, basis),
      el("div", { class: "ai-text" }, stage.ai_answer.text),
      el("div", { class: "ai-label" }, stage.ai_answer.label),
      ...stage.ai_answer.references.map((r) => el("div", {}, el("a", { class: "link", href: r, target: "_blank", rel: "noopener noreferrer" }, r))));
  }
  const items = stage.app_hits.length
    ? stage.app_hits.map((h) => el("div", { class: "case" }, el("b", {}, h.title), el("div", { class: "t-sub" }, "원인: " + (h.cause || "-") + " · 조치: " + h.action + (h.tcode ? " · " + h.tcode : ""))))
    : stage.gk_hits.map((h) => el("div", {}, el("a", { class: "link", href: h.link, target: "_blank", rel: "noopener noreferrer" }, h.title)));
  return el("div", { class: "step hit" }, head, ...items);
}

// 구조화 요약(요청 사항·기한·필요 조치). 서버가 마스킹해 저장한 AI 요약을 글자로만 표시한다
function digestView(task) {
  if (!task.digest) return task.summary_pending ? el("div", { class: "d-digest t-sub" }, "요약 준비 중…") : null;
  return el("div", { class: "d-digest" }, el("div", { class: "sec" }, icon("sparkles"), "AI 요약"),
    el("div", { class: "ai-text" }, task.digest), el("div", { class: "ai-label" }, "AI 생성 · 검증 필요"));
}

// 외부 회사명 확정(결정기록 0027): 같은 도메인 전체에 적용(source=user). 사용자 확정값은 버튼 없음
function companyConfirmButton(task, onChanged) {
  if (task.requester_kind !== "external" || !task.requester_domain || task.requester_source === "user") return null;
  return el("button", { type: "button", class: "btn sm req-confirm", title: task.requester_domain + " 도메인 전체에 적용",
    onclick: async () => {
      const current = task.requester_source === "domain" ? "" : (task.requester_label || "");
      const name = window.prompt(task.requester_domain + " 의 회사명(50자 이하)", current);
      if (name === null || !name.trim()) return;
      const result = await api("PUT", "/api/companies", { domain: task.requester_domain, company: name.trim() });
      if (!result.ok) { toast(errorText(result, "저장하지 못했습니다"), { kind: "error" }); return; }
      toast("회사명을 확정했습니다 · 같은 도메인 업무에 모두 적용");
      if (onChanged) onChanged();
    } }, "회사명 확정");
}

// suggest: false = 비슷한 사례 찾기만, true = 사례를 근거로 AI 해결 방안까지
// 유사 이력(G-Knowledge 검색)·AI 해결 방안은 필요한 사이트 로그인을 먼저 확인한다(결정기록 0034)
function runTaskSearch(task, suggest, box) {
  ensureLogin(suggest ? ["ai-ground"] : ["g-knowledge"], () => searchTask(task, suggest, box),
    { label: suggest ? "AI 해결 방안" : "유사 이력 찾기" });
}
async function searchTask(task, suggest, box) {
  box.replaceChildren(el("div", { class: "empty" }, suggest ? "비슷한 사례를 찾고 해결 방안을 만드는 중…" : "찾는 중…"));
  const result = await api("POST", "/api/search", { query: task.title, task_id: task.id, start_from: "app", suggest });
  if (!result.ok) { box.replaceChildren(el("div", { class: "empty" }, errorText(result, "검색하지 못했습니다"))); return; }
  const data = result.data;
  fill(box, ...data.stages.map(stageView),
    data.message ? el("div", { class: "empty" }, data.message) : null,
    el("div", { class: "actions" },
      !suggest ? el("button", { class: "btn sm ai", onclick: () => runTaskSearch(task, true, box) }, icon("sparkles"), "이 사례로 해결 방안 만들기") : null,
      el("button", { class: "btn sm", onclick: async () => { await api("POST", "/api/search/feedback", { task_id: task.id, helpful: true }); toast("평가를 남겼습니다"); } }, "도움 됨"),
      el("button", { class: "btn sm", onclick: async () => { await api("POST", "/api/search/feedback", { task_id: task.id, helpful: false }); toast("평가를 남겼습니다"); } }, "도움 안 됨")));
}

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

// G-Knowledge 등록 버튼(결정기록 0035): 진행 중이면 막고, 결과 미확인이면 확인·표시·재시도를 고르게 한다
function gkButtons(task, onChanged, b) {
  const st = task.gk_state ? task.gk_state.state : null;
  if (!task.gk_link && (st === "queued" || st === "running")) {
    const busy = b("gk main", "refresh-cw", gkStateText(task.gk_state), null);
    busy.disabled = true;
    return [busy];
  }
  if (!task.gk_link && st === "unknown") {
    return [b("gk main", "alert-triangle", "등록됨으로 표시", () => markGkRegistered(task.id, onChanged)),
      b("", null, "다시 시도", () => retryGkUnknown(task.id, onChanged))];
  }
  if (task.gk_link) return [];  // 중복 등록 불가(GK-06)
  return [b("gk main", "book-open", st === "failed_unsent" ? "G-Knowledge 다시 등록" : "G-Knowledge 지식 등록", () => openGkPreview(task.id, onChanged))];
}

// 상세 '지식 등록' 칸: 등록 링크 또는 진행 상태(텍스트로만)
function gkStateNode(task) {
  const st = task.gk_state;
  const link = task.gk_link ? el("a", { class: "link", href: task.gk_link, target: "_blank", rel: "noopener noreferrer" }, "등록됨") : null;
  if (!st) return link || "미등록";
  const chip = el("span", { class: "gk-st s-" + st.state }, st.state === "running" ? icon("refresh-cw") : null, gkStateText(st));
  return link ? el("span", {}, link, " ", chip) : chip;
}

// 회신 대기·팔로업(결정기록 0038): 초안 복사(발송 없음)·대기 해제·직접 표시
const FOLLOWUP_OPEN = ["waiting", "in_progress", "done_candidate", "on_hold"];
function followupButtons(task, onChanged, b) {
  if (!FOLLOWUP_OPEN.includes(task.status)) return [];
  const post = (path, done) => async () => {
    const result = await api("POST", "/api/tasks/" + task.id + path);
    toast(result.ok ? done : errorText(result, "처리하지 못했습니다"), { kind: result.ok ? "ok" : "error" });
    if (result.ok) onChanged();
  };
  const f = task.followup || { state: "none" };
  if (f.state === "none") return [b("", "mail", "회신 대기로 표시", post("/awaiting", "회신 대기로 표시했습니다"))];
  return [b("", "repeat", "팔로업 메일 초안 복사", () => copyFollowup(task)), b("", null, "대기 해제", post("/awaiting/clear", "회신 대기를 해제했습니다"))];
}
async function copyFollowup(task) {
  const result = await api("GET", "/api/tasks/" + task.id + "/followup-draft");
  if (!result.ok) { toast(errorText(result, "초안을 만들지 못했습니다"), { kind: "error" }); return; }
  const ok = await copyText(result.data.text, null);
  toast(ok ? "팔로업 메일 초안을 복사했습니다 · 메일 프로그램에 붙여넣어 보내세요(앱은 발송하지 않음)" : "복사하지 못했습니다. 다시 시도해 주세요", { kind: ok ? "ok" : "warn", ms: 6000 });
}
function followupText(task) {
  const f = task.followup;
  if (!f || f.state === "none") return "-";
  return (f.state === "followup" ? "팔로업 필요" : "회신 대기") + " · " + f.since.slice(5, 16).replace("T", " ") + " 보냄 · " + f.days + "근무일 경과";
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
      return [...gkButtons(task, onChanged, b), b("", "file-text", "히스토리 카드", () => cardEditor(task, resultBox))];
    default:
      return [];
  }
}

function renderDetailEmpty() {
  resetMail();
  fill(document.getElementById("d-body"), el("div", { class: "d-empty" }, icon("inbox"),
    el("div", {}, "업무를 선택하세요"), el("div", { class: "t-sub" }, "↑↓ 선택 · Enter 열기 · ←→ 페이지")));
  document.getElementById("d-foot").replaceChildren();
}

async function renderDetail(taskId, onChanged) {
  const result = await api("GET", "/api/tasks/" + taskId);
  const body = document.getElementById("d-body");
  const foot = document.getElementById("d-foot");
  if (!result.ok) { resetMail(); body.replaceChildren(el("div", { class: "empty" }, errorText(result, "불러오지 못했습니다"))); foot.replaceChildren(); return; }
  const task = result.data;
  const resultBox = el("div", { class: "d-result" });  // 버튼을 누르면 그때 내용이 생긴다(빈 상자를 미리 두지 않음)
  const refs = task.mail_refs || [];
  const received = refs.length ? (refs[0].received_at || "").slice(5, 16).replace("T", " ") : "-";
  // '원본 보기' = 가운데 원본 메일 영역 보이기/숨기기(글자·눌림 상태는 detail_mail.js가 맞춘다)
  const mailButton = refs.length ? el("button", { type: "button", class: "btn sm", onclick: toggleMail }, icon("mail"), "원본 보기") : null;
  const tools = el("div", { class: "actions tight d-tools" }, mailButton,
    el("button", { type: "button", class: "btn sm", onclick: () => runTaskSearch(task, false, resultBox) }, icon("search"), "유사 이력"),
    el("button", { type: "button", class: "btn sm ai", onclick: () => runTaskSearch(task, true, resultBox) }, icon("sparkles"), "AI 해결 방안"));
  // 배치(결정기록 0022): 작업 버튼 행 → 검색·해결 방안 결과 → 원본 메일(발신자·수신 일시·본문, 내부 스크롤) → 하단 상태 버튼
  fill(body,
    el("div", { class: "d-badges" }, categoryTag(task.module_tag), statusBadge(task)),
    el("h3", { class: "d-title", title: task.title }, task.title),
    el("div", { class: "meta" },
      el("div", {}, "요청 부서"), el("div", {}, requesterNode(task, "") || "-", companyConfirmButton(task, onChanged)),
      el("div", {}, "기한"), el("div", {}, dueText(task)),
      el("div", {}, "수신"), el("div", { class: "mono" }, received),
      el("div", { class: "ev" }, "근거"), el("div", { class: "ev" }, task.evidence ? "“" + task.evidence + "”" : "-"),
      el("div", {}, "지식 등록"), el("div", { id: "d-gk-state" }, gkStateNode(task)),
      el("div", {}, "회신"), el("div", { id: "d-followup" }, followupText(task))),
    // 진행 요약(맨 위, 접기 가능) → 구조화 요약 → 진행 경과 타임라인(결정기록 0028)
    progressSection(task),
    digestView(task),
    tools,
    resultBox,
    timelineSection(task),
    mailSection(task.id, refs, mailButton));
  fill(foot, el("div", { class: "d-btns" }, ...footButtons(task, resultBox, onChanged), ...followupButtons(task, onChanged,
    (cls, ic, label, onclick) => el("button", { type: "button", class: "btn " + cls, onclick }, ic ? icon(ic) : null, label))),
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
