// 상태 배지·업무 구분 배지(결정기록 0022). 색은 status.css 토큰. 모든 글자는 el()/textContent로만 넣는다.
"use strict";

const STATUS_BADGE_CLASS = {
  suggested: "b-prop", waiting: "b-wait", in_progress: "b-prog",
  done_candidate: "b-cand", done: "b-done", on_hold: "b-hold",
};

function statusBadge(task) {
  return el("span", { class: "badge " + (STATUS_BADGE_CLASS[task.status] || "b-wait") }, task.status_label);
}

// 구분 판별 기준: 서버 설정(전자결재 구분 이름·기본 구분=목록 첫 항목). 읽기 전에는 기본값으로 그린다.
const CATEGORY_KINDS = { approval: "전자결재", general: "일반업무" };

async function loadCategoryKinds() {
  const result = await api("GET", "/api/task-categories");
  if (!result.ok) return;
  if (result.data.approval_category) CATEGORY_KINDS.approval = result.data.approval_category;
  if (result.data.categories && result.data.categories.length) CATEGORY_KINDS.general = result.data.categories[0];
}

function categoryKind(name) {
  if (name === CATEGORY_KINDS.approval) return { cls: "cat-appr", ic: "file-check", label: "전자결재 업무" };
  if (name === CATEGORY_KINDS.general) return { cls: "cat-gen", ic: "briefcase", label: "일반 업무" };
  return { cls: "cat-etc", ic: "briefcase", label: "업무 구분" };
}

// extraClass: 목록이 좁을 때 쓰는 보조 줄 등 배치용 클래스
function categoryTag(name, extraClass) {
  if (!name) return null;
  const kind = categoryKind(name);
  return el("span", { class: "cat-tag " + kind.cls + (extraClass ? " " + extraClass : ""), title: kind.label + ": " + name },
    icon(kind.ic), el("span", {}, name));
}
