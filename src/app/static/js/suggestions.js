// 제안된 업무: 승인·수정 후 승인·무시(TODO-02). 승인 결과는 서버에서 이벤트로 기록된다.
"use strict";

const MODULES = ["", "FI", "CO", "MM", "SD", "PP", "HR", "Basis", "ABAP"];

async function loadSuggestions() {
  const result = await api("GET", "/api/tasks?status=suggested");
  const body = document.getElementById("sugg");
  if (!result.ok) { body.replaceChildren(); return; }
  if (!result.data.length) {
    body.replaceChildren(el("tr", {}, el("td", { colspan: "4" }, el("div", { class: "empty" }, "제안된 업무가 없습니다."))));
    return;
  }
  const rows = await Promise.all(result.data.map(async (task) => {
    const detail = await api("GET", "/api/tasks/" + task.id);
    const evidence = detail.ok && detail.data.evidence ? "“" + detail.data.evidence + "”" : "(근거 문장은 수집 직후에만 표시)";
    const title = el("input", { class: "inline-input", value: task.title, maxlength: "200" });
    const due = el("input", { class: "inline-input", type: "date", value: task.due_at ? task.due_at.slice(0, 10) : "" });
    const module = el("select", { class: "inline-input" }, ...MODULES.map((m) => el("option", { value: m, selected: m === (task.module_tag || "") }, m || "모듈")));
    const approve = async () => {
      const edit = {};
      if (title.value !== task.title) edit.title = title.value;
      if (due.value && due.value !== (task.due_at || "").slice(0, 10)) edit.due_date = due.value;
      if (module.value && module.value !== task.module_tag) edit.module_tag = module.value;
      const r = await api("POST", "/api/tasks/" + task.id + "/approve", Object.keys(edit).length ? edit : undefined);
      toast(r.ok ? "할 일 목록에 추가했습니다" : errorText(r, "승인하지 못했습니다"));
      loadSuggestions();
    };
    const dismiss = async () => {
      const r = await api("POST", "/api/tasks/" + task.id + "/dismiss");
      toast(r.ok ? "무시했습니다" : errorText(r, "처리하지 못했습니다"));
      loadSuggestions();
    };
    return el("tr", { dataset: { id: String(task.id) } },
      el("td", {}, title, el("div", { class: "t-sub" }, task.requester_dept || "-", " ", module)),
      el("td", { class: "t-sub" }, evidence),
      el("td", {}, due),
      el("td", {}, el("div", { class: "actions tight" },
        el("button", { class: "btn sm primary", onclick: approve }, "승인"),
        el("button", { class: "btn sm", onclick: dismiss }, "무시"))));
  }));
  body.replaceChildren(...rows);
}

document.getElementById("btn-collect").addEventListener("click", async () => {
  const result = await api("POST", "/api/mail/collect", { period: "week" });
  toast(result.ok ? "제안 " + result.data.suggested + "건 · 제외 " + result.data.excluded_messages + "건" : errorText(result, "수집하지 못했습니다"));
  loadSuggestions();
});
document.getElementById("btn-approve-all").addEventListener("click", async () => {
  if (!window.confirm("수정 없이 모두 승인할까요?")) return;
  const result = await api("GET", "/api/tasks?status=suggested");
  for (const task of result.data || []) await api("POST", "/api/tasks/" + task.id + "/approve");
  toast("모두 승인했습니다");
  loadSuggestions();
});
loadSuggestions();
