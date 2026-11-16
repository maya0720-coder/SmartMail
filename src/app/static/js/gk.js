// G-Knowledge 등록 미리보기 모달(GK-01·02·06). 등록 전 G-Knowledge 로그인을 확인한다(GK-05, 결정기록 0034).
// 등록은 백그라운드로 진행한다(결정기록 0035): 서버가 바로 접수(202)하고, 진행은 작업 센터·상세 상태로 본다.
"use strict";

let gkTaskId = null;
let gkDone = null;
let gkRetryUnknown = false;  // 결과 미확인 건을 중복 경고 확인 후 다시 보낼 때

// opts.retryUnknown: '확인 필요' 건 다시 시도(사용자가 중복 경고를 확인함)
async function openGkPreview(taskId, onDone, opts) {
  const result = await api("GET", "/api/gk/preview/" + encodeURIComponent(taskId));
  if (!result.ok) { toast(errorText(result, "미리보기를 만들지 못했습니다")); return; }
  const data = result.data;
  if (data.already_registered) { toast("이미 G-Knowledge에 등록한 업무입니다(중복 등록 불가)"); return; }
  gkTaskId = taskId;
  gkDone = onDone || null;
  gkRetryUnknown = Boolean(opts && opts.retryUnknown);
  document.getElementById("gk-title").value = data.title;
  document.getElementById("gk-body").value = data.body;
  document.getElementById("gk-tag").value = data.tags.join(", ");
  document.getElementById("gk-err").textContent = "";
  openDlg("dlg-gk");
}

async function submitGk() {
  const payload = {
    title: document.getElementById("gk-title").value,
    body: document.getElementById("gk-body").value,
    tags: document.getElementById("gk-tag").value.split(",").map((t) => t.trim()).filter(Boolean).slice(0, 10),
    retry_unknown: gkRetryUnknown,
  };
  const send = async () => {
    const result = await api("POST", "/api/gk/register/" + encodeURIComponent(gkTaskId), payload);
    if (!result.ok) {
      document.getElementById("gk-err").textContent = errorText(result, "등록을 시작하지 못했습니다");
      return;
    }
    closeDlg("dlg-gk");
    toast("등록을 시작했습니다 · 진행은 작업 센터에서 확인");
    if (typeof refreshJobs === "function") refreshJobs(true);
    if (gkDone) gkDone(result.data);
  };
  ensureLogin(["g-knowledge"], send, { label: "지식 등록" });  // 필요할 때만 로그인(결정기록 0034)
}

// '확인 필요'(등록요청 뒤 결과 미확인) 건: 사용자가 G-Knowledge에서 확인한 뒤 '등록됨'으로 표시
async function markGkRegistered(taskId, onDone) {
  const result = await api("POST", "/api/gk/mark-registered/" + encodeURIComponent(taskId), {});
  if (!result.ok) { toast(errorText(result, "표시하지 못했습니다"), { kind: "error" }); return; }
  toast("등록됨으로 표시했습니다", { kind: "ok" });
  if (typeof refreshJobs === "function") refreshJobs(true);
  if (onDone) onDone();
}

// '확인 필요' 건 다시 시도: 중복 등록 위험을 알리고 확인을 받는다
function retryGkUnknown(taskId, onDone) {
  const ok = window.confirm("이전 등록 요청의 결과를 확인하지 못했습니다.\n"
    + "G-Knowledge에 이미 등록됐을 수 있어 다시 보내면 중복될 수 있습니다.\n먼저 G-Knowledge에서 확인하셨나요? 그래도 다시 보낼까요?");
  if (ok) openGkPreview(taskId, onDone, { retryUnknown: true });
}

document.getElementById("gk-cancel").addEventListener("click", () => closeDlg("dlg-gk"));
document.getElementById("gk-submit").addEventListener("click", submitGk);
