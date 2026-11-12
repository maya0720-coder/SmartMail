// G-Knowledge 등록 미리보기 모달(GK-01·02·06). 등록 전 G-Knowledge 로그인을 요구한다(GK-05).
"use strict";

let gkTaskId = null;
let gkDone = null;

async function openGkPreview(taskId, onDone) {
  const result = await api("GET", "/api/gk/preview/" + encodeURIComponent(taskId));
  if (!result.ok) { toast(errorText(result, "미리보기를 만들지 못했습니다")); return; }
  const data = result.data;
  if (data.already_registered) { toast("이미 G-Knowledge에 등록한 업무입니다(중복 등록 불가)"); return; }
  gkTaskId = taskId;
  gkDone = onDone || null;
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
  };
  const send = async () => {
    const result = await api("POST", "/api/gk/register/" + encodeURIComponent(gkTaskId), payload);
    if (!result.ok) {
      if (result.status === 503 && /로그인/.test(errorText(result, ""))) { requireLogin("g-knowledge", send); return; }
      document.getElementById("gk-err").textContent = errorText(result, "등록하지 못했습니다");
      return;
    }
    closeDlg("dlg-gk");
    toast("G-Knowledge에 등록했습니다");
    if (gkDone) gkDone(result.data);
  };
  await send();
}

document.getElementById("gk-cancel").addEventListener("click", () => closeDlg("dlg-gk"));
document.getElementById("gk-submit").addEventListener("click", submitGk);
