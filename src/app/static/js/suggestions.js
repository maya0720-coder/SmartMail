// 제안된 업무 화면 진입점(결정기록 0015): 트리아지 목록·상세(triage_list.js·triage_detail.js) + AI 요청 요약 상태 막대.
"use strict";

// ---- AI 요청 요약 상태(결정기록 0014) ----
let lastDone = null;
let sumTimer = 0;
const SUMMARY_POLL_RUNNING_MS = 1000;  // 실행 중에는 진행률이 바로 보이게 짧게
const SUMMARY_POLL_IDLE_MS = 15000;

// 진행률 막대: 실행 중에만 보인다(결정기록 0023). 글자는 textContent로만 넣는다.
function renderSummaryProgress(s) {
  const box = document.getElementById("sum-prog-box");
  const show = Boolean(s.running && s.total);
  box.classList.toggle("hidden", !show);
  if (!show) return;
  const total = Number(s.total) || 0;
  const completed = Math.min(Number(s.completed) || 0, total);
  const pct = total ? Math.round((completed / total) * 100) : 0;
  const bar = document.getElementById("sum-prog");
  bar.setAttribute("aria-valuenow", String(pct));
  bar.setAttribute("aria-valuetext", "완료 " + completed + " / 전체 " + total);
  document.getElementById("sum-prog-bar").style.width = pct + "%";
  document.getElementById("sum-pct").textContent = pct + "%";
  document.getElementById("sum-count").textContent = "완료 " + completed + " / 전체 " + total;
}
async function loadSummaryStatus() {
  const result = await api("GET", "/api/summaries/status");
  if (!result.ok) return;
  const s = result.data;
  const text = document.getElementById("sum-text");
  const button = document.getElementById("sum-run");
  renderSummaryProgress(s);
  if (!s.pending) text.textContent = "모든 업무 제목이 요청 요약으로 바뀌었습니다.";
  else if (s.running) text.textContent = "AI-Ground로 요청 요약을 만드는 중… (남은 " + s.pending + "건)";
  else if (!s.ready) text.textContent = "요약 대기 " + s.pending + "건 · AI-Ground에 로그인하면 메일 제목 대신 요청 요약을 보여 줍니다.";
  else text.textContent = "요약 대기 " + s.pending + "건 · 2분마다 조금씩 자동으로 바꿉니다" + (s.message ? " (" + s.message + ")" : "");
  button.disabled = !s.pending || s.running;
  // 요약 진행·완료 시 보이는 항목의 칩·제목만 갱신(전체 다시 그리기 없음, 결정기록 0029)
  const progressKey = s.done + ":" + s.completed + ":" + s.running;
  if (lastDone !== null && progressKey !== lastDone) patchSummaryStates();
  lastDone = progressKey;
  clearTimeout(sumTimer);
  sumTimer = setTimeout(loadSummaryStatus, s.running ? SUMMARY_POLL_RUNNING_MS : SUMMARY_POLL_IDLE_MS);
}
async function runSummaries() {
  const result = await api("POST", "/api/summaries/run");
  if (!result.ok) { toast(errorText(result, "시작하지 못했습니다"), { kind: "error" }); return; }
  toast("요청 요약을 시작했습니다. 많으면 몇 분에 걸쳐 나눠 처리합니다.");
  setTimeout(loadSummaryStatus, 800);
}
// 로그인이 필요하면 일괄 로그인 창을 띄우고, 로그인되면 요약을 바로 시작한다(결정기록 0034)
document.getElementById("sum-run").addEventListener("click", () => ensureLogin(["ai-ground"], runSummaries, { label: "AI 일괄 요약" }));

loadSummaryStatus();
loadTriage();

// 제안 목록 패널 크기 조절(결정기록 0016·0033): 고정 상한 없이 화면 폭의 70%까지.
// 상세는 420px 아래로 줄지 않는다(상세 필드가 2열로 바뀌는 지점 고려)
initSplitter({ handle: document.getElementById("split-list"), panel: document.getElementById("tr-list"), other: document.getElementById("tr-detail"),
  cssVar: "--tr-list-w", prefKey: "triage_list_w", side: "left", min: 280, maxRatio: 0.7, otherMin: 420 });
