// 제안된 업무: 승인·수정 후 승인·무시(TODO-02). 승인 결과는 서버에서 이벤트로 기록된다.
"use strict";

// 구분 목록(사용자 정의, 결정기록 0014). 화면을 열 때 서버에서 받는다.
let categories = [];

async function loadSuggestions() {
  if (!categories.length) {
    const cats = await api("GET", "/api/task-categories");
    categories = cats.ok ? cats.data.categories : [];
  }
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
    const module = el("select", { class: "inline-input", "aria-label": "구분" },
      ...categories.map((name) => el("option", { value: name, selected: name === task.module_tag }, name)));
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
    // 요청 요약을 아직 만들지 않은 업무는 메일 제목에서 온 임시 제목임을 표시한다
    const pendingTag = task.summary_pending ? el("span", { class: "chip sum-wait", title: "AI 요청 요약 대기 중(지금은 메일 제목)" }, "요약 대기") : null;
    return el("tr", { dataset: { id: String(task.id) } },
      el("td", {}, title, el("div", { class: "t-sub sugg-meta" }, module, pendingTag, el("span", {}, task.requester_dept || ""))),
      el("td", { class: "t-sub evid", title: evidence }, el("span", { class: "clamp2" }, evidence)),
      el("td", {}, due),
      el("td", {}, el("div", { class: "actions tight" },
        el("button", { class: "btn sm primary", onclick: approve }, icon("check"), "승인"),
        el("button", { class: "btn sm", onclick: dismiss }, icon("x"), "무시"))));
  }));
  body.replaceChildren(...rows);
}

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
  if (lastDone !== null && s.done !== lastDone) loadSuggestions();  // 요약이 생기면 목록 갱신
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

document.getElementById("btn-collect").addEventListener("click", async () => {
  const result = await api("POST", "/api/mail/collect", { period: "sync" });
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
