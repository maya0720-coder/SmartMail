// 상세 패널 가운데(작업 버튼 행과 하단 상태 버튼 사이)의 원본 메일 영역(MAIL-08~10, 결정기록 0022).
// 원문은 저장하지 않고 업무를 고를 때 원본에서 다시 읽는다. 보고 있는 업무의 메일 1통만 메모리에 두고,
// 업무가 바뀌면 지운다. '원본 보기' 버튼이 이 영역의 보이기/숨기기를 맡는다. 글자는 el()/textContent로만.
"use strict";

const MAIL = {
  taskId: null,     // 지금 영역이 속한 업무
  refId: null,      // 보여 줄 메일 참조 ID
  order: [],        // 업무의 메일 참조 ID(시간순) — 이전/다음 이동용
  visible: false,
  dismissed: null,  // 사용자가 숨긴 메일(화면 갱신 때 다시 펼치지 않는다)
  cache: null,      // { refId, data } — 같은 업무를 다시 그릴 때 원본을 또 읽지 않도록(업무가 바뀌면 지움)
  seq: 0,           // 빠르게 선택을 바꾸면 마지막 요청 결과만 그린다
  box: null,
  button: null,
};

function resetMail() {
  MAIL.seq++;
  Object.assign(MAIL, { taskId: null, refId: null, order: [], visible: false, cache: null, box: null, button: null });
}

function mailButtonState() {
  const button = MAIL.button;
  if (!button) return;
  const shown = MAIL.visible && MAIL.refId !== null;
  button.replaceChildren(icon("mail"), shown ? "원본 숨기기" : "원본 보기");
  button.setAttribute("aria-pressed", shown ? "true" : "false");
  button.setAttribute("aria-expanded", shown ? "true" : "false");
  button.classList.toggle("on", shown);
}

function mailNotice(text) {
  if (MAIL.box) MAIL.box.replaceChildren(el("div", { class: "empty" }, text));
}

// 이전/다음 메일 이동(결정기록 0028). 시간순 목록에서 한 칸씩
function stepButtons() {
  const index = MAIL.order.indexOf(MAIL.refId);
  if (MAIL.order.length < 2 || index < 0) return null;
  const go = (step) => () => openMail(MAIL.order[index + step]);
  return el("span", { class: "dm-step" },
    el("button", { type: "button", class: "icon-btn sm", "aria-label": "이전 메일", title: "이전 메일", disabled: index === 0, onclick: go(-1) }, icon("chevron-left")),
    el("span", { class: "t-sub mono" }, (index + 1) + "/" + MAIL.order.length),
    el("button", { type: "button", class: "icon-btn sm", "aria-label": "다음 메일", title: "다음 메일", disabled: index === MAIL.order.length - 1, onclick: go(1) }, icon("chevron-right")));
}

function drawMail(m) {
  if (!MAIL.box) return;
  if (typeof markTimeline === "function") markTimeline(MAIL.refId);
  if (m.missing) { fill(MAIL.box, el("div", { class: "dm-head" }, icon("mail"), el("h4", { class: "dm-subject" }, "원문 없음"), stepButtons()), el("div", { class: "empty" }, m.notice)); return; }
  const received = (m.received_at || "").slice(0, 16).replace("T", " ");
  const body = el("div", { class: "dm-body", tabindex: "0", "aria-label": "원본 메일 본문" },
    el("div", { class: "mail-full" }, m.body || ""),
    m.attachments.length ? el("div", { class: "t-sub mt8" }, "첨부(열지 않음): " + m.attachments.join(", ")) : null);
  fill(MAIL.box,
    el("div", { class: "dm-head" }, icon("mail"),
      el("h4", { class: "dm-subject", title: m.subject || "" }, m.subject || "원본 메일"),
      stepButtons(),
      m.can_open ? el("button", { type: "button", class: "btn sm", onclick: async () => {
        const r = await api("POST", "/api/mail/" + MAIL.refId + "/open");
        if (!r.ok) toast(errorText(r, "열지 못했습니다"), { kind: "error" });
      } }, "아웃룩에서 열기") : null),
    el("dl", { class: "dm-meta" },
      el("div", {}, el("dt", {}, "보낸 사람"), el("dd", {}, m.sender_name || "-")),
      el("div", {}, el("dt", {}, "수신 일시"), el("dd", { class: "mono" }, received || "-"))),
    body);
  body.scrollTop = 0;
}

async function loadMail(refId) {
  const seq = ++MAIL.seq;
  mailNotice("원본을 읽는 중…");
  const result = await api("GET", "/api/mail/" + refId);
  if (seq !== MAIL.seq || MAIL.refId !== refId || !MAIL.visible) return;  // 그 사이 다른 업무·메일을 골랐거나 숨겼다
  if (!result.ok) { mailNotice(errorText(result, "원본을 읽지 못했습니다")); return; }
  MAIL.cache = { refId, data: result.data };
  drawMail(result.data);
}

function renderMailState() {
  mailButtonState();
  const box = MAIL.box;
  if (!box) return;
  const shown = MAIL.visible && MAIL.refId !== null;
  box.classList.toggle("hidden", !shown);
  if (!shown) { MAIL.seq++; box.replaceChildren(); return; }
  if (MAIL.cache && MAIL.cache.refId === MAIL.refId) drawMail(MAIL.cache.data);
  else loadMail(MAIL.refId);
}

// 상세를 그릴 때 부른다. button = '원본 보기' 버튼(없으면 null). 메일이 없으면 null을 돌려준다.
function mailSection(taskId, refs, button) {
  if (!refs.length) { resetMail(); return null; }
  if (MAIL.taskId !== taskId || !refs.some((r) => r.id === MAIL.refId)) {
    MAIL.seq++;
    MAIL.taskId = taskId;
    MAIL.refId = refs[refs.length - 1].id;  // 기본은 최신 메일(결정기록 0028)
    MAIL.cache = null;
    MAIL.visible = MAIL.dismissed !== MAIL.refId;  // 업무를 고르면 최신 원본을 바로 보여 준다(숨긴 메일 제외)
  }
  MAIL.order = refs.map((r) => r.id);
  MAIL.button = button;
  MAIL.box = el("section", { class: "d-mail", id: "d-mail", "aria-label": "원본 메일" });
  if (button) button.setAttribute("aria-controls", "d-mail");
  renderMailState();
  return MAIL.box;
}

function revealMail() {
  if (MAIL.box && MAIL.visible) MAIL.box.scrollIntoView({ block: "nearest" });
}

// '원본 보기' 버튼: 가운데 영역 보이기/숨기기
function toggleMail() {
  if (MAIL.refId === null) return;
  MAIL.visible = !MAIL.visible;
  MAIL.dismissed = MAIL.visible ? null : MAIL.refId;
  renderMailState();
  revealMail();
}

// 다른 원본(같은 업무의 후속 메일)을 가운데 영역에 연다
function openMail(refId) {
  MAIL.refId = refId;
  MAIL.visible = true;
  MAIL.dismissed = null;
  renderMailState();
  revealMail();
}
