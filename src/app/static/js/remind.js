// 주간보고 마감 알림 배너(U6, 결정기록 0037). 모든 화면 위쪽에 보인다.
// 서버가 마감 시각·이번 주 게시 여부·'오늘 숨기기'를 판단한다(숨김은 서버 메모리, 브라우저 저장소 안 씀).
"use strict";

const REMIND_POLL_MS = 600000;  // 10분마다 다시 확인

async function refreshRemind() {
  const bar = document.getElementById("remind-bar");
  if (!bar || document.hidden) return;
  const result = await api("GET", "/api/weekly/reminder");
  bar.classList.toggle("hidden", !(result.ok && result.data.show));
}

document.getElementById("remind-hide").addEventListener("click", async () => {
  const result = await api("POST", "/api/weekly/reminder/hide");
  if (result.ok) document.getElementById("remind-bar").classList.add("hidden");
});
document.addEventListener("visibilitychange", refreshRemind);
setInterval(refreshRemind, REMIND_POLL_MS);
refreshRemind();
