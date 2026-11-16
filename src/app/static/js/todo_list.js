// 내 할 일 목록 도우미: 정렬·페이지 크기·상태 분포 도넛(디자인 명세 01·03). 페이저(paginate·renderPager)는 pager.js.
// 정렬·페이지·선택 상태는 메모리 변수에만 둔다(브라우저 저장소 금지). 모든 글자는 el()/textContent로만 넣는다.
"use strict";

const MIN_PER_PAGE_SHELL = 3;
const MIN_PER_PAGE_DOC = 5;
const DEFAULT_ROW_HEIGHT = 40;
const DOC_PAGER_RESERVE = 72;
const DOC_BOTTOM_GAP = 24;
// 앱 셸(한 화면 고정형) 조건: app.css의 @media와 같아야 한다
const SHELL_QUERY = "(min-width: 1025px) and (min-height: 700px)";

function isShell() {
  return document.body.dataset.page === "dashboard" && window.matchMedia(SHELL_QUERY).matches;
}

// 정렬: key = "recent"(기본, 스레드 마지막 메일 최근순) | "title" | "due", dir = 1(오름) | -1(내림)
// 마지막 메일 시각이 없으면 요청일, 그것도 없으면 생성 순서(ID)로 둔다(결정기록 0028)
const recentOf = (t) => String(t.last_mail_at || t.requested_at || "");
function sortTasks(list, sort) {
  const copy = list.slice();
  if (sort.key === "recent") {
    copy.sort((a, b) => -sort.dir * (recentOf(b).localeCompare(recentOf(a)) || b.id - a.id));
  } else if (sort.key === "title") {
    copy.sort((a, b) => sort.dir * a.title.localeCompare(b.title, "ko"));
  } else if (sort.key === "due") {
    // 기한 없는 업무는 방향과 관계없이 맨 뒤
    copy.sort((a, b) => (!a.due_at) - (!b.due_at) || sort.dir * String(a.due_at).localeCompare(String(b.due_at)));
  } else {
    copy.sort((a, b) => b.id - a.id);
  }
  return copy;
}

// 머리칸 클릭 순환: 오름 → 내림 → 해제(기본 정렬)
function nextSort(sort, key) {
  if (sort.key !== key) return { key, dir: 1 };
  if (sort.dir === 1) return { key, dir: -1 };
  return { key: "recent", dir: -1 };
}

// 페이지 크기. 앱 셸: 목록 영역(#todo-wrap)의 실제 높이 기준. 문서형: 창 높이 − 표 위치 기준.
function computePerPage(wrap, tbody) {
  const first = tbody.querySelector("tr.row");
  const rowHeight = (first && first.getBoundingClientRect().height) || DEFAULT_ROW_HEIGHT;
  if (isShell()) {
    const head = wrap.querySelector("thead");
    const room = wrap.clientHeight - (head ? head.getBoundingClientRect().height : 0);
    return Math.max(MIN_PER_PAGE_SHELL, Math.floor(room / rowHeight));
  }
  const top = tbody.getBoundingClientRect().top + window.scrollY;
  const room = window.innerHeight - Math.min(top, window.innerHeight * 0.6) - DOC_PAGER_RESERVE - DOC_BOTTOM_GAP;
  return Math.max(MIN_PER_PAGE_DOC, Math.floor(room / rowHeight));
}

// 상태 분포 도넛(새 API 없이 allTasks로 계산). 범례 6개를 항상 보여 주고(0건은 흐리게), 누르면 필터를 바꾼다.
const DONUT_STATES = [
  ["waiting", "대기"], ["suggested", "제안됨"], ["in_progress", "진행 중"],
  ["done_candidate", "완료 후보"], ["on_hold", "보류"], ["done", "완료"],
];
// 필터 버튼이 없는 상태(대기·보류)는 '전체'로 둔다
const DONUT_FILTER = { suggested: "suggested", in_progress: "in_progress", done_candidate: "done_candidate", done: "done" };

function drawDonut(box, tasks, onPick) {
  const live = tasks.filter((t) => t.status !== "dismissed");
  const counts = DONUT_STATES.map(([key, label]) => ({ key, label, n: live.filter((t) => t.status === key).length }));
  const total = live.length;
  const r = 15.915;  // 둘레 100
  const svg = svgEl("svg", { viewBox: "0 0 42 42", class: "donut-svg", role: "img", "aria-label": "업무 상태 분포 " + total + "건" });
  svg.append(svgEl("circle", { class: "donut-track", cx: 21, cy: 21, r }));
  let offset = 25;
  for (const c of counts) {
    if (!c.n) continue;
    const pct = (c.n / total) * 100;
    svg.append(svgEl("circle", { class: "donut-seg s-" + c.key, cx: 21, cy: 21, r, "stroke-dasharray": pct + " " + (100 - pct), "stroke-dashoffset": offset }));
    offset -= pct;
  }
  const label = svgEl("text", { class: "donut-num", x: 21, y: 23.5, "text-anchor": "middle" });
  label.textContent = String(total);
  svg.append(label);
  box.replaceChildren(
    el("div", { class: "donut" }, svg,
      el("ul", { class: "legend" }, ...counts.map((c) => el("li", {},
        el("button", { type: "button", class: "lg-item" + (c.n ? "" : " zero"), title: c.label + " " + c.n + "건 · 눌러서 목록 거르기",
          onclick: () => onPick(DONUT_FILTER[c.key] || "all") },
          el("i", { class: "dot s-" + c.key }), el("span", {}, c.label), el("b", {}, String(c.n))))))));
}
