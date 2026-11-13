// 업무 상태 변경·되돌리기·일괄 처리(디자인 명세 03, 4-1·4-4).
// 상태 전이 규칙은 화면에서 판단하지 않는다. 서버가 거부한 변경은 실패로 집계해 안내한다(도메인 규칙 중복 금지).
"use strict";

const STATUS_LABEL = {
  suggested: "제안됨", waiting: "대기", in_progress: "진행 중", done_candidate: "완료 후보",
  on_hold: "보류", done: "완료", dismissed: "무시",
};
const BULK_CONCURRENCY = 3;  // 동시 호출 상한

// 업무 1건에 행동 1개를 보낸다. action: "approve" | "dismiss" | 상태값
function sendAction(taskId, action) {
  if (action === "approve") return api("POST", "/api/tasks/" + taskId + "/approve");
  if (action === "dismiss") return api("POST", "/api/tasks/" + taskId + "/dismiss");
  return api("POST", "/api/tasks/" + taskId + "/status", { status: action });
}

// 상세·키보드에서 쓰는 단건 처리. 상태 변경은 토스트의 [되돌리기]로 이전 상태로 돌린다.
// 승인(제안됨 → 대기)·무시는 되돌리는 전이가 없으므로 되돌리기를 보이지 않는다.
async function applyAction(task, action, onChanged) {
  if (action === "dismiss" && !window.confirm("'" + task.title + "' 업무를 무시할까요? 되돌릴 수 없습니다.")) return;
  const result = await sendAction(task.id, action);
  if (!result.ok) {
    toast(errorText(result, "바꾸지 못했습니다"), { kind: "error" });
    return;
  }
  const label = action === "approve" ? "대기(승인)" : STATUS_LABEL[action] || action;
  const undoable = action !== "approve" && action !== "dismiss";
  const previous = task.status;
  toast("'" + task.title + "' → " + label, undoable ? {
    action: "되돌리기",
    onAction: async () => {
      const back = await sendAction(task.id, previous);
      toast(back.ok ? "되돌렸습니다" : "되돌릴 수 없는 변경입니다", { kind: back.ok ? "ok" : "warn" });
      await onChanged();
    },
  } : {});
  await onChanged();
}

// 여러 건을 동시 3개 이하로 처리하고 성공·실패 건수를 돌려준다.
async function runBulk(ids, action) {
  const queue = ids.slice();
  let ok = 0;
  let failed = 0;
  async function worker() {
    while (queue.length) {
      const id = queue.shift();
      const result = await sendAction(id, action);
      if (result.ok) ok += 1; else failed += 1;
    }
  }
  await Promise.all(Array.from({ length: Math.min(BULK_CONCURRENCY, ids.length) }, worker));
  return { ok, failed };
}

async function bulkAction(ids, action, onChanged) {
  if (!ids.length) return;
  if (action === "dismiss" && !window.confirm(ids.length + "건을 무시할까요? 되돌릴 수 없습니다.")) return;
  const { ok, failed } = await runBulk(ids, action);
  toast("성공 " + ok + "건" + (failed ? " · 실패 " + failed + "건(허용되지 않는 변경)" : ""), { kind: failed ? "warn" : "ok" });
  await onChanged();
}
