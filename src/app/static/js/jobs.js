// 작업 센터(U1, 결정기록 0035): 상단 바 버튼 + 진행 중 건수 배지 + 패널.
// 서버(/api/jobs)가 여러 백그라운드 작업 상태를 합쳐 준다. 사유는 코드 값만 받아 여기서 문구로 바꾼다.
"use strict";

const GK_STATE_LABELS = {
  queued: "등록 대기", running: "등록 중", requested: "등록 완료(검토 대기)",
  failed_unsent: "실패 · 다시 시도", unknown: "확인 필요",
};
const GK_STEP_LABELS = { open: "화면 여는 중", analyze: "AI 분석 중", submit: "등록요청 중" };
const GK_REASON_LABELS = {
  login_required: "G-Knowledge 로그인 필요", not_configured: "화면 필드 설정 확인 필요",
  busy: "G-Knowledge가 바빠 보내지 않음", body_check: "본문 입력 확인 실패(보내지 않음)",
  analyze_timeout: "AI 분석 시간 초과(보내지 않음)", failed: "등록 실패(보내지 않음)",
  duplicate: "이미 등록됐거나 같은 제목이 있음", app_restart: "앱이 다시 시작되어 중단",
  result_unknown: "등록요청 후 결과를 확인하지 못함", error: "오류 · 결과 확인 필요",
  manual: "직접 확인해 등록됨으로 표시", login_required_ai: "AI-Ground 로그인 필요",
};
// 위키 양식 읽기 실패 사유(결정기록 0037)
const WIKI_REASON_LABELS = { login_required: "위키 로그인 필요", not_found: "내 영역 머리글을 찾지 못함", invalid: "양식 구조를 읽지 못함", failed: "본문 읽기 실패" };
const JOB_NAMES = { gk: "G-Knowledge 등록", summary: "AI 요청 요약", mail: "메일 수집", company: "회사명 조회", wiki: "위키 양식 읽기" };
const JOBS_FAST_MS = 3000;   // 진행 중인 작업이 있을 때
const JOBS_SLOW_MS = 20000;  // 없을 때(새로 시작된 백그라운드 작업 확인용)

const JOBS = { timer: 0, seen: new Map(), first: true };

function gkStateText(state) {
  if (!state) return "";
  if (state.state === "running" && state.step) return "등록 중 · " + (GK_STEP_LABELS[state.step] || "진행 중");
  const label = GK_STATE_LABELS[state.state] || state.state;
  return state.reason && state.state !== "requested" ? label + " · " + (GK_REASON_LABELS[state.reason] || "사유 확인 필요") : label;
}

function jobStateText(job) {
  if (job.kind === "gk") return gkStateText(job);
  if (job.kind === "summary") {
    if (job.state === "running") return "진행 중 " + job.completed + " / " + job.total;
    return "대기 " + job.pending + "건" + (job.reason ? " · AI-Ground 로그인하면 자동 진행" : "");
  }
  if (job.kind === "mail") return job.state === "running" ? "수집 중…" : job.at ? "마지막 수집 " + job.at.slice(5, 16).replace("T", " ") : "아직 수집 안 함";
  if (job.kind === "company") return job.state === "running" ? "조회 중…" : "최근 " + job.saved + "건 확인";
  if (job.kind === "wiki") return job.state === "running" ? "읽는 중…" : job.state === "done" ? "양식 확인 완료" : "실패 · " + (WIKI_REASON_LABELS[job.reason] || "사유 확인 필요");
  return job.state;
}

function jobActions(job) {
  const b = (label, onclick) => el("button", { type: "button", class: "btn sm", onclick }, label);
  if (job.kind === "gk" && job.state === "unknown") {
    return [b("등록됨으로 표시", () => markGkRegistered(job.task_id, () => refreshJobs(true))),
      b("다시 시도", () => openGkTask(job.task_id, true))];
  }
  if (job.kind === "gk" && job.state === "failed_unsent") return [b("다시 시도", () => openGkTask(job.task_id, false))];
  if (job.kind === "summary" && job.retry) {
    return [b("지금 요약", () => ensureLogin(["ai-ground"], async () => {
      const r = await api("POST", "/api/summaries/run");
      toast(r.ok ? "요청 요약을 시작했습니다" : errorText(r, "시작하지 못했습니다"), { kind: r.ok ? "ok" : "error" });
      refreshJobs(true);
    }, { label: "AI 일괄 요약" }))];
  }
  if (job.kind === "summary" && job.reason) return [b("로그인", () => ensureLogin(["ai-ground"], () => refreshJobs(true), { force: true, label: "AI-Ground 연결" }))];
  return [];
}

// 등록 미리보기는 대시보드에만 있다. 다른 화면에서는 대시보드로 이동해 연다(?gk=업무번호)
function openGkTask(taskId, unknown) {
  if (typeof openGkPreview !== "function") { location.href = "/?gk=" + encodeURIComponent(taskId) + (unknown ? "&retry=1" : ""); return; }
  const done = () => { refreshJobs(true); document.dispatchEvent(new CustomEvent("gk-changed", { detail: { taskId } })); };
  if (unknown) retryGkUnknown(taskId, done); else openGkPreview(taskId, done);
}

function renderJobs(data) {
  const badge = document.getElementById("jobs-badge");
  badge.textContent = String(data.active);
  badge.classList.toggle("hidden", !data.active);
  const failed = data.jobs.filter((j) => j.group === "failed").length;
  document.getElementById("jobs-btn").classList.toggle("has-fail", failed > 0);
  document.getElementById("jobs-btn").setAttribute("aria-label", "작업 센터 · 진행 중 " + data.active + "건" + (failed ? " · 확인 필요 " + failed + "건" : ""));
  const list = document.getElementById("jobs-list");
  if (!data.jobs.length) { fill(list, el("li", { class: "jobs-empty" }, "진행 중이거나 최근 끝난 작업이 없습니다")); return; }
  fill(list, ...data.jobs.map((job) => el("li", { class: "job g-" + job.group, "data-job": job.id },
    el("div", { class: "job-top" }, el("b", {}, JOB_NAMES[job.kind] || job.kind),
      job.at ? el("span", { class: "job-at mono" }, job.at.slice(11, 16)) : null),
    job.target ? el("div", { class: "job-target", title: job.target }, job.target) : null,
    el("div", { class: "job-state" }, jobStateText(job)),
    el("div", { class: "job-actions" }, ...jobActions(job)))));
}

// 완료·실패로 바뀐 작업은 토스트를 한 번 띄운다(처음 읽을 때는 띄우지 않음)
function noticeJobChanges(jobs) {
  const changed = [];
  for (const job of jobs) {
    const before = JOBS.seen.get(job.id);
    const now = job.kind === "gk" ? job.state : job.group;
    JOBS.seen.set(job.id, now);
    if (JOBS.first || before === now) continue;
    changed.push(job);
    if (job.kind !== "gk") continue;
    if (job.state === "requested") toast("G-Knowledge 등록 완료(검토 대기) · " + (job.target || ""), { kind: "ok" });
    else if (job.state === "failed_unsent" || job.state === "unknown") {
      toast("G-Knowledge " + gkStateText(job), { kind: "warn", action: "작업 센터", onAction: () => toggleJobs(true) });
    }
  }
  JOBS.first = false;
  if (changed.length) document.dispatchEvent(new CustomEvent("jobs-changed", { detail: changed }));
}

async function refreshJobs(soon) {
  clearTimeout(JOBS.timer);
  if (document.hidden && !soon) { JOBS.timer = setTimeout(refreshJobs, JOBS_SLOW_MS); return; }
  const result = await api("GET", "/api/jobs");
  let fast = false;
  if (result.ok) {
    renderJobs(result.data);
    noticeJobChanges(result.data.jobs);
    fast = result.data.active > 0;
  }
  JOBS.timer = setTimeout(refreshJobs, soon || fast ? JOBS_FAST_MS : JOBS_SLOW_MS);
}

function toggleJobs(open) {
  const panel = document.getElementById("jobs-panel");
  const button = document.getElementById("jobs-btn");
  const show = open === undefined ? panel.classList.contains("hidden") : open;
  panel.classList.toggle("hidden", !show);
  button.setAttribute("aria-expanded", String(show));
  if (show) { refreshJobs(true); panel.focus(); }
}

document.getElementById("jobs-btn").addEventListener("click", () => toggleJobs());
document.getElementById("jobs-panel").addEventListener("keydown", (event) => {
  if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); toggleJobs(false); document.getElementById("jobs-btn").focus(); }
});
document.addEventListener("pointerdown", (event) => {
  const wrap = document.getElementById("jobs-wrap");
  if (!wrap.contains(event.target) && !document.getElementById("jobs-panel").classList.contains("hidden")) toggleJobs(false);
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshJobs(true); });
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => refreshJobs());
else refreshJobs();
