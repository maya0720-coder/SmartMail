// 주간보고 조직 양식 프로필 편집 보조(N4·U5, WIKI-01·02, 결정기록 0037).
// - 구조 잠금: 양식 머리글·표 열 이름은 기본 수정 불가(회색 잠금). '구조 잠금 해제'로 풀 수 있다.
// - 양식과 비교: 머리글·표 열 순서가 양식과 다른 곳을 표시한다.
// - 양식으로 되돌리기: 내용은 두고 머리글·표 열 구조만 양식대로 되돌린다.
// - 업무 넣기: 커서가 표 안이면 양식의 열 연결대로 표 행을 만든다.
// - 지난주 이월: 지난주 '차주 계획'을 완료·진행·유지 후보로 보여 주고 고른 것만 편집기에 넣는다.
// 화면 상태는 메모리에만 둔다. 모든 글자는 el()/textContent로만 넣는다.
"use strict";

const WP = { profile: null, locked: true, form: null };
const CARRY_LABELS = { done: "완료 → 금주 실적", progress: "진행 중 → 금주 실적(진행)", keep: "손대지 않음 → 차주 계획 유지" };

function structuralNodes() {
  return Array.from(richEditor().children).filter((n) => ["H3", "H4", "TABLE"].includes(n.tagName));
}
function profileShape() {
  return WP.profile ? WP.profile.blocks.map((b, i) => ({ ...b, index: i })).filter((b) => b.kind === "heading" || b.kind === "table") : [];
}
function headerRow(table) { return table.querySelector("tr"); }
function columnNames(table) {
  const row = headerRow(table);
  return row ? Array.from(row.children).map((c) => c.textContent.trim()) : [];
}

// ---- 구조 잠금 ----
function applyLock() {
  const on = Boolean(WP.profile) && WP.locked;
  for (const node of richEditor().querySelectorAll("h3, h4, th")) {
    if (on) { node.setAttribute("contenteditable", "false"); node.classList.add("pf-locked"); }
    else { node.removeAttribute("contenteditable"); node.classList.remove("pf-locked"); }
  }
  const button = document.getElementById("wk-lock");
  button.setAttribute("aria-pressed", on ? "true" : "false");
  button.textContent = on ? "구조 잠금 해제" : "구조 잠그기";
}
function toggleLock() {
  WP.locked = !WP.locked;
  applyLock();
  if (!WP.locked) compareProfile(true);
}

// 초안을 그린 뒤 부른다(weekly.js loadDraft)
function setProfile(profile) {
  WP.profile = profile;
  WP.locked = true;
  for (const id of ["wk-lock", "wk-compare", "wk-restore"]) document.getElementById(id).classList.toggle("hidden", !profile);
  document.getElementById("wk-profile-note").classList.toggle("hidden", Boolean(profile) || !document.getElementById("wk-link").value);
  applyLock();
}

// ---- 양식과 비교 ----
function compareProfile(quiet) {
  if (!WP.profile) return 0;
  for (const node of richEditor().querySelectorAll(".pf-diff")) node.classList.remove("pf-diff");
  const want = profileShape();
  const nodes = structuralNodes();
  let diff = Math.abs(want.length - nodes.length);
  want.forEach((block, i) => {
    const node = nodes[i];
    if (!node) return;
    const same = block.kind === "table"
      ? node.tagName === "TABLE" && columnNames(node).join("\u0001") === block.columns.join("\u0001")
      : node.tagName === "H" + block.level && node.textContent.trim() === block.text;
    if (!same) { node.classList.add("pf-diff"); diff++; }
  });
  for (const node of nodes.slice(want.length)) node.classList.add("pf-diff");
  if (!quiet) toast(diff ? "양식과 다른 곳 " + diff + "군데를 표시했습니다" : "양식 구조와 같습니다", { kind: diff ? "warn" : "ok" });
  return diff;
}

// ---- 양식으로 되돌리기(내용 유지) ----
function headingNode(block) { return el("h" + block.level, {}, block.text); }
function fixTable(table, block) {
  const width = block.columns.length;
  const head = headerRow(table);
  const names = block.columns.map((c) => el("th", {}, c));
  if (head) head.replaceChildren(...names); else table.prepend(el("thead", {}, el("tr", {}, ...names)));
  for (const row of Array.from(table.querySelectorAll("tr")).slice(1)) {
    while (row.children.length > width) row.lastElementChild.remove();
    while (row.children.length < width) row.append(el("td", {}, el("br")));
  }
}
function tableNode(block) {
  const table = el("table", {}, el("thead", {}, el("tr")), el("tbody", {}, el("tr")));
  fixTable(table, block);
  return table;
}
function restoreProfile() {
  if (!WP.profile) return;
  const nodes = structuralNodes();
  let j = 0;
  for (const block of profileShape()) {
    const node = nodes[j];
    const kindOk = node && (block.kind === "table" ? node.tagName === "TABLE" : node.tagName !== "TABLE");
    if (kindOk) {
      if (block.kind === "table") fixTable(node, block);
      else if (node.tagName !== "H" + block.level || node.textContent.trim() !== block.text) node.replaceWith(headingNode(block));
      j++;
    } else {
      const fresh = block.kind === "table" ? tableNode(block) : headingNode(block);
      if (node) node.before(fresh); else richEditor().append(fresh);
    }
  }
  for (const extra of nodes.slice(j)) if (extra.tagName !== "TABLE") extra.replaceWith(el("p", {}, extra.textContent));
  WP.locked = true;
  applyLock();
  compareProfile(true);
  richChanged();
  toast("양식 구조로 되돌렸습니다(내용은 그대로)");
}

// ---- 업무를 표 행으로 넣기 ----
const ROW_VALUE = {
  title: (t) => t.title, module: (t) => t.module_tag || "기타", dept: (t) => t.requester_label || t.requester_dept || "-",
  status: (t) => t.status_label || "", due: (t) => (t.due_at ? t.due_at.slice(5, 10).replace("-", "/") : "-"),
  closed: (t) => (t.closed_at ? t.closed_at.slice(5, 10).replace("-", "/") : "-"), progress: (t) => t.progress_digest || "", note: () => "",
};
function tableFields(table) {
  const width = columnNames(table).length;
  if (WP.profile) {
    const tables = profileShape().filter((b) => b.kind === "table");
    const order = structuralNodes().filter((n) => n.tagName === "TABLE").indexOf(table);
    const block = tables[order];
    const fields = block && WP.profile.bindings.columns[String(block.index)];
    if (fields && fields.length === width) return fields;
  }
  return ["title", ...Array(Math.max(0, width - 1)).fill("note")];
}
// 처리했으면 true(weekly.js 업무 목록 버튼에서 부름)
function insertTaskRow(task) {
  const cell = selectionCell();
  if (!cell) return false;
  const table = cell.closest("table");
  const row = el("tr", { class: "carry-add" }, ...tableFields(table).map((f) => el("td", {}, ROW_VALUE[f](task) || el("br"))));
  const body = table.querySelector("tbody") || table;
  body.append(row);
  richChanged();
  return true;
}

// ---- 지난주 이월 ----
function sectionHeadingText(source) {
  if (WP.profile) {
    const hit = Object.entries(WP.profile.bindings.headings).find(([, s]) => s === source);
    if (hit) return WP.profile.blocks[Number(hit[0])].text;
  }
  return WP.form ? WP.form.headings[source] : "";
}
// 머리글 아래 첫 목록(없으면 만든다)에 항목을 더한다. 표가 먼저 나오면 표 행으로 더한다
function appendUnder(headingText, text, cls) {
  const heading = Array.from(richEditor().querySelectorAll("h3, h4")).find((h) => h.textContent.trim() === headingText);
  if (!heading) { richEditor().append(el("ul", {}, el("li", { class: cls }, text))); return; }
  let node = heading.nextElementSibling;
  while (node && !["H3", "H4"].includes(node.tagName)) {
    if (node.tagName === "UL" || node.tagName === "OL") {
      const blank = Array.from(node.children).find((li) => !li.textContent.trim());
      if (blank) blank.replaceWith(el("li", { class: cls }, text)); else node.append(el("li", { class: cls }, text));
      return;
    }
    if (node.tagName === "TABLE") {
      const width = columnNames(node).length;
      const cells = [el("td", {}, text), ...Array.from({ length: Math.max(0, width - 1) }, () => el("td", {}, el("br")))];
      (node.querySelector("tbody") || node).append(el("tr", { class: cls }, ...cells));
      return;
    }
    node = node.nextElementSibling;
  }
  heading.after(el("ul", {}, el("li", { class: cls }, text)));
}

async function openCarry() {
  const result = await api("POST", "/api/weekly/carryover");
  if (!result.ok) { toast(errorText(result, "지난주 보고를 읽지 못했습니다"), { kind: "error" }); return; }
  if (!result.data.source) { toast("지난주 게시 기록을 찾지 못했습니다(위키 세션 없음·게시 캐시 없음)", { kind: "warn" }); return; }
  const items = result.data.items;
  document.getElementById("carry-src").textContent = (result.data.source === "wiki" ? "위키 지난주 페이지의 내 영역" : "게시 캐시(마스킹 사본)") + " · 차주 계획 " + items.length + "건";
  fill(document.getElementById("carry-list"), ...["done", "progress", "keep"].map((kind) => {
    const group = items.filter((i) => i.kind === kind);
    if (!group.length) return null;
    return el("fieldset", { class: "carry-group carry-" + kind }, el("legend", {}, CARRY_LABELS[kind] + " " + group.length),
      ...group.map((item) => el("label", { class: "carry-item" },
        el("input", { type: "checkbox", checked: true, dataset: { kind, text: item.text } }), item.text)));
  }));
  openDlg("dlg-carry");
  const first = document.querySelector("#carry-list input");
  if (first) first.focus();
}

function applyCarry() {
  const picked = Array.from(document.querySelectorAll("#carry-list input:checked"));
  setMode("rich");
  for (const box of picked) {
    const { kind, text } = box.dataset;
    if (kind === "keep") appendUnder(sectionHeadingText("planned"), text, "carry-keep");
    else appendUnder(sectionHeadingText("done"), kind === "progress" ? text + " (진행)" : text, kind === "progress" ? "carry-change" : "carry-add");
  }
  closeDlg("dlg-carry");
  applyLock();
  richChanged();
  toast("지난주 항목 " + picked.length + "건을 넣었습니다(초록: 완료, 노랑: 진행, 회색: 계획 유지)");
}

document.getElementById("wk-lock").addEventListener("click", toggleLock);
document.getElementById("wk-compare").addEventListener("click", () => compareProfile(false));
document.getElementById("wk-restore").addEventListener("click", restoreProfile);
document.getElementById("btn-carry").addEventListener("click", openCarry);
document.getElementById("carry-ok").addEventListener("click", applyCarry);
document.getElementById("carry-cancel").addEventListener("click", () => closeDlg("dlg-carry"));
