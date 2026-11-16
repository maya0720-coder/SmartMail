// 대시보드 성과 지표 카드·표(디자인 명세 03, 결정기록 0039).
// - 카드 기준 basis: current(기본, 진행 중인 현재 기간 vs 직전 기간 같은 경과 구간) | last_complete(이전 방식)
// - 대시보드 기간(currentGrain)이 '전체'면 지표 카드는 '연간'으로 보여 준다.
// 모든 글자는 el()/textContent로만 넣는다.
"use strict";

let metricBasis = "current";  // '기준·표로 보기'의 토글(메모리에만)
let periodStart = null;  // 현재 기간 시작(할 일 목록 since 값, '전체'면 null)

const METRIC_DEFS = {
  "MET-01": "기간 내 완료 확정 업무 수(지표 원장 기준 · 도넛의 '완료'는 지금 완료 상태인 업무 수)",
  "MET-02": "기한 내 완료 ÷ 기한이 있는 완료 업무",
  "MET-03": "G-Knowledge 등록 완료 업무 ÷ 완료 업무",
  "MET-04": "앱·G-Knowledge에서 해결 ÷ 유사 업무 검색",
};
const METRIC_ICONS = { "MET-01": "check", "MET-02": "target", "MET-03": "book-open", "MET-04": "repeat" };
const GRAIN_LABEL = { all: "전체", year: "연간", half: "반기", quarter: "분기", month: "월", week: "주(목~수)" };

const metricGrain = () => (currentGrain === "all" ? "year" : currentGrain);

function deltaText(card, compareLabel) {
  if (card.delta === null) return compareLabel + " -";
  const sign = card.delta >= 0 ? "+" : "";
  return compareLabel + " " + (card.metric === "MET-01" ? sign + Math.round(card.delta) + "건" : sign + Math.round(card.delta * 100) + "%p");
}

async function loadMetrics() {
  const grain = metricGrain();
  const result = await api("GET", "/api/metrics?grain=" + encodeURIComponent(grain) + "&basis=" + metricBasis);
  if (!result.ok) return;
  const data = result.data;
  periodStart = currentGrain === "all" ? null : data.period_start;
  const current = data.basis === "current";
  const compareLabel = current ? data.compare_label : "직전 대비";
  const range = data.buckets.length ? data.buckets[0].label + " ~ " + data.buckets[data.buckets.length - 1].label : "";
  const scope = current ? data.period_text + " · " + data.compare_label + "(" + data.compare_text + ")" : "최근 완료 기간 vs 직전 기간 · 그래프 " + range;
  document.getElementById("mk").replaceChildren(...data.cards.map((card) => {
    const cur = card.current;
    const definition = METRIC_DEFS[card.metric];
    const trendClass = card.trend === "개선" ? "up" : card.trend === "악화" ? "dn" : "";
    return el("div", { class: "kpi", title: definition + " · 표본 " + cur.sample + "건 · " + scope },
      el("div", { class: "l" }, icon(METRIC_ICONS[card.metric] || "target"), el("span", {}, card.name),
        cur.reference_only ? el("span", { class: "ref" }, "참고") : null),
      el("div", { class: "vrow" },
        el("div", { class: "v" }, formatMetric(card.metric, cur.value), el("span", { class: "u" }, card.metric === "MET-01" ? "건" : "")),
        el("div", { class: "chartbox" }, drawChart(card.metric, data.buckets, data.target_due_rate, definition))),
      el("div", { class: "dl" }, deltaText(card, compareLabel), card.trend ? " · " : "", el("span", { class: trendClass }, card.trend || "")));
  }));
  const period = document.getElementById("m-period");
  if (period) period.textContent = current ? data.period_text : "최근 완료 기간 기준";
  const desc = GRAIN_LABEL[grain] + " 기준 · " + scope + " · 진행 중 기간은 그래프에서 흐리게 표시 · 기한 준수율 목표 " +
    Math.round(data.target_due_rate * 100) + "%(예시, IE팀 확정 전) · 카드 '완료 건수'는 지표 원장(기간 안 완료 확정), 도넛 '완료'는 지금 완료 상태인 업무 수";
  document.getElementById("m-desc").textContent = desc;
  document.getElementById("toggle-table").title = desc;
  const header = el("tr", {}, el("th", {}, "기간"), ...Object.values(data.names).map((n) => el("th", {}, n)));
  const rows = data.buckets.map((b) => el("tr", {}, el("td", {}, b.label + (b.partial ? " (진행 중)" : "")),
    ...Object.keys(data.names).map((m) => el("td", {}, formatMetric(m, b.values[m].value) + " (n=" + b.values[m].sample + ")"))));
  document.getElementById("m-tbl").replaceChildren(el("table", {}, el("thead", {}, header), el("tbody", {}, rows)));
}

// AI 품질(최근 30일): 해결 방안 '도움 됨' 비율·AI 호출 성공률(결정기록 0021)
async function showAiQuality() {
  const box = document.getElementById("m-ai");
  const result = await api("GET", "/api/metrics/ai-quality");
  if (!box || !result.ok) return;
  const q = result.data;
  const pct = (v) => (v === null ? "-" : Math.round(v * 100) + "%");
  box.textContent = "AI 품질(최근 " + q.window_days + "일) · 해결 방안 '도움 됨' " + pct(q.helpful_rate)
    + " (" + q.helpful + "/" + (q.helpful + q.not_helpful) + "건" + (q.reference_only ? ", 표본 적음·참고값" : "") + ")"
    + " · AI 호출 성공 " + pct(q.ai_success_rate) + " (" + q.ai_success + "/" + (q.ai_success + q.ai_failure) + "회)";
}

// 표로 보기: 화면을 밀어내지 않도록 모달로 연다
document.getElementById("toggle-table").addEventListener("click", (event) => {
  event.preventDefault();
  openDlg("dlg-mtbl");
  showAiQuality();
});
document.getElementById("mtbl-close").addEventListener("click", () => closeDlg("dlg-mtbl"));
document.getElementById("m-basis").addEventListener("change", (event) => {
  metricBasis = event.target.checked ? "last_complete" : "current";
  loadMetrics();
});
