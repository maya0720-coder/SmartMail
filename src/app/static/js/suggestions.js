// 제안된 업무 화면 진입점(결정기록 0015): 트리아지 목록·상세(triage_list.js·triage_detail.js) + AI 요청 요약 상태 막대.
"use strict";

// ---- AI 요청 요약 상태(결정기록 0014) ----
let lastDone = null;
let sumTimer = 0;
async function loadSummaryStatus() {
  const result = await api("GET", "/api/summaries/status");
  if (!result.ok) return;
  const s = result.data;
  const text = document.getElementById("sum-text");
  const button = document.getElementById("sum-run");
  if (!s.pending) text.textContent = "모든 업무 제목이 요청 요약으로 바뀌었습니다.";
  else if (s.running) text.textContent = "AI-Ground로 요청 요약을 만드는 중… (남은 " + s.pending + "건)";
  else if (!s.ready) text.textContent = "요약 대기 " + s.pending + "건 · AI-Ground에 로그인하면 메일 제목 대신 요청 요약을 보여 줍니다.";
  else text.textContent = "요약 대기 " + s.pending + "건 · 2분마다 조금씩 자동으로 바꿉니다" + (s.message ? " (" + s.message + ")" : "");
  button.disabled = !s.pending || s.running;
  button.textContent = s.ready ? "AI로 일괄 요약" : "AI-Ground 로그인";
  if (lastDone !== null && s.done !== lastDone) loadTriage();  // 요약이 생기면 목록 갱신
  lastDone = s.done;
  clearTimeout(sumTimer);
  sumTimer = setTimeout(loadSummaryStatus, s.running ? 2000 : 15000);
}
document.getElementById("sum-run").addEventListener("click", async () => {
  const status = await api("GET", "/api/summaries/status");
  if (status.ok && !status.data.ready) { requireLogin("ai-ground", loadSummaryStatus); return; }
  const result = await api("POST", "/api/summaries/run");
  if (!result.ok) { toast(errorText(result, "시작하지 못했습니다"), { kind: "error" }); return; }
  toast("요청 요약을 시작했습니다. 많으면 몇 분에 걸쳐 나눠 처리합니다.");
  setTimeout(loadSummaryStatus, 800);
});

loadSummaryStatus();
loadTriage();
