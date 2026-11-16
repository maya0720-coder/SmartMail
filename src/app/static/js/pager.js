// 공용 페이저(대시보드 '내 할 일'·제안된 업무 목록): 페이지 나누기와 하단 페이징 UI(.pg · .pg-info · .pager).
// 상태는 호출한 쪽이 메모리 변수로 가진다. 모든 글자는 el()/textContent로만 넣는다. todo_list.js보다 먼저 로드한다.
"use strict";

function paginate(list, page, perPage) {
  const total = list.length;
  const pages = Math.max(1, Math.ceil(total / perPage));
  const current = Math.min(Math.max(1, page), pages);
  const start = (current - 1) * perPage;
  return { items: list.slice(start, start + perPage), page: current, pages, start, end: Math.min(start + perPage, total), total };
}

// ‹ 1 2 … n › 형태. 현재 페이지는 aria-current="page"
function pageNumbers(page, pages) {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const nums = [...new Set([1, pages, page - 1, page, page + 1])].filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b);
  const out = [];
  nums.forEach((n, i) => { if (i && n - nums[i - 1] > 1) out.push("…"); out.push(n); });
  return out;
}

function renderPager(nav, info, view, perPage, onGo) {
  info.textContent = view.total ? (view.start + 1) + "–" + view.end + " / " + view.total + "건 · 페이지당 " + perPage + "건" : "0건";
  if (view.pages <= 1) { nav.replaceChildren(); return; }
  const button = (label, target, attrs) => el("button", { type: "button", class: "pg-btn", onclick: () => onGo(target), ...attrs }, label);
  nav.replaceChildren(
    button(icon("chevron-left"), view.page - 1, { "aria-label": "이전 페이지", disabled: view.page === 1 }),
    ...pageNumbers(view.page, view.pages).map((n) => n === "…"
      ? el("span", { class: "pg-gap", "aria-hidden": "true" }, "…")
      : button(String(n), n, n === view.page ? { "aria-current": "page", "aria-label": n + "페이지(현재)" } : { "aria-label": n + "페이지" })),
    button(icon("chevron-right"), view.page + 1, { "aria-label": "다음 페이지", disabled: view.page === view.pages }));
}
