// 설정·연결: 데이터 모드(실제 / 가상) 선택(결정기록 0042). 저장값은 다음 시작 때 적용된다.
"use strict";

const RUN_MODE_LABELS = { real: "실제 데이터", demo: "가상 데이터(데모)" };

function showRunMode(state) {
  document.getElementById("rm-current").textContent = "지금: " + RUN_MODE_LABELS[state.current];
  document.getElementById("rm-mode").value = state.saved || state.current;
  const note = state.forced
    ? "이번 실행은 실행 스크립트(-Demo 등)로 정해져 화면 선택보다 우선합니다."
    : state.restart_needed
      ? RUN_MODE_LABELS[state.saved] + "로 저장했습니다 · 앱을 종료한 뒤 다시 실행하면 적용됩니다."
      : "";
  document.getElementById("rm-note").textContent = note;
}

async function loadRunMode() {
  const result = await api("GET", "/api/run-mode");
  if (result.ok) showRunMode(result.data);
}

document.getElementById("rm-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const mode = document.getElementById("rm-mode").value;
  if (mode === "real" && !window.confirm("실제 데이터 모드는 아웃룩·사내 사이트와 연동합니다. 다음 시작부터 적용할까요?")) return;
  const result = await api("PUT", "/api/run-mode", { mode });
  if (!result.ok) { toast(errorText(result, "저장하지 못했습니다"), { kind: "error" }); return; }
  showRunMode(result.data);
  toast(result.data.restart_needed ? "저장했습니다 · 다시 시작하면 적용됩니다" : "저장했습니다");
});

loadRunMode();
