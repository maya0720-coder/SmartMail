// 상세 패널 '현재까지 진행 요약'과 '진행 경과' 타임라인(결정기록 0028).
// 규칙 요약은 서버가 저장된 방향·시각으로 만든다. AI 진행 요약은 마스킹된 메일별 요약만 보낸다.
// 보낸 사람 이름은 타임라인 API가 원본을 다시 읽어 응답에만 담는다(화면 메모리에만, 저장 금지).
// 모든 글자는 el()/textContent로만 넣는다.
"use strict";

const PROGRESS = { seq: 0, open: true };

function shortTime(iso) {
  return (iso || "").slice(5, 16).replace("T", " ");
}

function directionIcon(direction) {
  const out = direction === "out";
  return el("span", { class: "tl-dir " + (out ? "out" : "in"), title: out ? "보냄" : "받음", "aria-label": out ? "보냄" : "받음" },
    out ? "↗" : "↙");
}

// 진행 요약 영역: 규칙 요약 + (저장된) AI 진행 요약 + [AI로 진행 요약] 버튼
function progressSection(task) {
  if (!(task.mail_refs || []).length) return null;
  const aiBox = el("div", { class: "pg-ai" });
  const drawAi = (digest, stale) => {
    if (!digest) { aiBox.replaceChildren(); return; }
    fill(aiBox, el("div", { class: "ai-text" }, digest),
      el("div", { class: "ai-label" }, "AI 생성 · 검증 필요" + (stale ? " · 새 메일 있음 · 다시 요약" : "")));
    aiBox.classList.toggle("stale", Boolean(stale));
  };
  drawAi(task.progress_digest, task.progress_stale);
  const button = el("button", { type: "button", class: "btn sm ai" }, icon("sparkles"),
    task.progress_digest ? "AI로 다시 요약" : "AI로 진행 요약");
  // AI 진행 요약은 AI-Ground 로그인을 먼저 확인한다(결정기록 0034)
  button.addEventListener("click", () => ensureLogin(["ai-ground"], summarize, { label: "AI 진행 요약" }));
  async function summarize() {
    button.disabled = true;
    const result = await api("POST", "/api/tasks/" + task.id + "/progress-summary");
    button.disabled = false;
    if (!result.ok) { toast(errorText(result, "진행 요약을 만들지 못했습니다"), { kind: "error" }); return; }
    drawAi(result.data.progress_digest, result.data.progress_stale);
    button.replaceChildren(icon("sparkles"), "AI로 다시 요약");
  }
  const details = el("details", { class: "d-progress", open: PROGRESS.open ? "" : null },
    el("summary", {}, "현재까지 진행 요약"),
    el("div", { class: "pg-rule" }, task.progress || "-"),
    aiBox,
    el("div", { class: "actions tight" }, button));
  details.addEventListener("toggle", () => { PROGRESS.open = details.open; });
  return details;
}

// 타임라인: 저장된 참조로 먼저 그리고, 보낸 사람 이름은 원본을 읽어 채운다
function timelineSection(task) {
  const refs = task.mail_refs || [];
  if (!refs.length) return null;
  const rows = new Map();
  const list = el("ol", { class: "d-timeline", "aria-label": "진행 경과(시간순)" },
    ...refs.map((ref) => {
      const who = el("span", { class: "tl-who" });
      const row = el("li", {},
        el("button", { type: "button", class: "tl-row", dataset: { ref: String(ref.id) }, onclick: () => openMail(ref.id) },
          directionIcon(ref.direction), el("span", { class: "tl-time mono" }, shortTime(ref.received_at)),
          el("span", { class: "tl-title" }, ref.title || (ref.summary_state === "missing" ? "원문 없음" : "요약 없음")), who));
      rows.set(ref.id, { row, who });
      return row;
    }));
  const seq = ++PROGRESS.seq;
  api("GET", "/api/tasks/" + task.id + "/timeline").then((result) => {
    if (seq !== PROGRESS.seq || !result.ok) return;
    for (const item of result.data.items) {
      const found = rows.get(item.id);
      if (!found) continue;
      if (item.missing) { found.who.textContent = "원문 없음"; found.row.classList.add("missing"); }
      else if (item.sender_name) found.who.textContent = item.sender_name;
    }
  });
  return el("details", { class: "d-progress", open: "" }, el("summary", {}, "진행 경과 " + refs.length + "통"), list);
}

// 원본 영역에서 보고 있는 메일을 타임라인에 표시한다(detail_mail.js가 부른다)
function markTimeline(refId) {
  for (const node of document.querySelectorAll(".d-timeline .tl-row")) {
    const on = node.dataset.ref === String(refId);
    node.classList.toggle("on", on);
    node.setAttribute("aria-current", on ? "true" : "false");
  }
}
