// 체크(일괄 대상) 공통 함수(B1·B2, TODO-02, 결정기록 0040). 대시보드·제안된 업무 화면이 함께 쓴다.
// - '선택'(상세에 보이는 1건)과 '체크'(일괄 대상)는 분리한다. 행 클릭은 체크를 건드리지 않는다.
// - 체크 한도는 서버 일괄 한도와 같은 값(body data-bulk-max, app/triage_tokens.py MAX_BULK)
// 체크 상태는 메모리에만 둔다. 모든 글자는 el()/textContent로만 넣는다.
"use strict";

const BULK_LIMIT = (() => {
  const n = Number(document.body.dataset.bulkMax);
  return Number.isInteger(n) && n > 0 ? n : 100;
})();
const BULK_LIMIT_TEXT = BULK_LIMIT + "건까지 선택했습니다 · 처리 후 다시 선택해 주세요";

// 한 건 체크 토글. 한도에 막히면 안내하고 false
function toggleOne(set, id) {
  if (set.has(id)) { set.delete(id); return true; }
  if (set.size >= BULK_LIMIT) { toast(BULK_LIMIT_TEXT, { kind: "warn" }); return false; }
  set.add(id);
  return true;
}

// 여러 건 체크/해제. 체크는 앞에서부터 한도까지만 넣고, 넘치면 안내한다
function setMany(set, ids, on) {
  if (!on) { for (const id of ids) set.delete(id); return; }
  let capped = false;
  for (const id of ids) {
    if (set.has(id)) continue;
    if (set.size >= BULK_LIMIT) { capped = true; break; }
    set.add(id);
  }
  if (capped) toast(BULK_LIMIT_TEXT, { kind: "warn" });
}

// 묶음의 체크 상태: none | some | all
function groupState(set, ids) {
  const n = ids.filter((id) => set.has(id)).length;
  if (!ids.length || n === 0) return "none";
  return n === ids.length ? "all" : "some";
}

// 3상태 체크박스 표시(일부면 indeterminate, aria-checked=mixed)
function headState(input, state) {
  input.checked = state === "all";
  input.indeterminate = state === "some";
  input.setAttribute("aria-checked", state === "some" ? "mixed" : state === "all" ? "true" : "false");
}

// 3상태 체크박스를 눌렀을 때: 전부 체크면 해제, 아니면 체크
function toggleGroup(set, ids) {
  setMany(set, ids, groupState(set, ids) !== "all");
}

// ▾ 선택 메뉴: entries = [[글자, 동작, 비활성 여부], ...]. 바깥 클릭·Esc로 닫는다
function openBulkMenu(button, entries) {
  closeBulkMenu();
  const menu = el("div", { class: "popover bulk-menu", id: "bulk-menu", role: "menu" },
    ...entries.map(([label, action, disabled]) => el("button", { type: "button", class: "btn sm ghost", role: "menuitem", disabled: Boolean(disabled),
      onclick: () => { closeBulkMenu(); action(); } }, label)));
  button.parentElement.append(menu);
  button.setAttribute("aria-expanded", "true");
  const first = menu.querySelector("button:not([disabled])");
  if (first) first.focus();
}
function closeBulkMenu() {
  const menu = document.getElementById("bulk-menu");
  if (!menu) return false;
  const button = menu.parentElement.querySelector("[aria-haspopup]");
  if (button) button.setAttribute("aria-expanded", "false");
  menu.remove();
  return true;
}
document.addEventListener("click", (event) => {
  const menu = document.getElementById("bulk-menu");
  if (menu && !menu.parentElement.contains(event.target)) closeBulkMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && closeBulkMenu()) { event.preventDefault(); event.stopImmediatePropagation(); }
}, true);
