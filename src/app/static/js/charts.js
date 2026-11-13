// 성과 지표 작은 추이 그래프(SVG, 120×28). 완료 건수는 막대, 나머지는 선(기한 준수율은 목표 점선). 진행 중 기간은 흐리게.
// 표본이 모두 0이면 평평한 선 대신 "데이터 없음"을 보여 준다(디자인 명세 03, 3-3). 툴팁 첫 줄은 지표 정의.
// svgEl은 common.js의 공용 함수를 쓴다.
"use strict";

let chartTip = null;
function showTip(event, title, rows, definition) {
  if (!chartTip) { chartTip = el("div", { class: "tip" }); document.body.append(chartTip); }
  chartTip.replaceChildren(
    ...(definition ? [el("div", { class: "tip-def" }, definition)] : []),
    el("b", {}, title),
    ...rows.map(([k, v]) => el("div", { class: "r" }, el("span", {}, k), el("span", {}, v))));
  chartTip.style.display = "block";
  chartTip.style.left = Math.min(event.clientX + 12, window.innerWidth - 220) + "px";
  chartTip.style.top = (event.clientY + 12) + "px";
}
function hideTip() { if (chartTip) chartTip.style.display = "none"; }

function formatMetric(metric, value) {
  if (value === null || value === undefined) return "-";
  return metric === "MET-01" ? String(Math.round(value)) : Math.round(value * 100) + "%";
}

// buckets: [{label, partial, values: {metric: {value, sample, reference_only}}}]
function drawChart(metric, buckets, target, definition) {
  if (!buckets.some((b) => b.values[metric].sample > 0)) {
    return el("span", { class: "nodata", title: definition || "" }, "데이터 없음");
  }
  const width = 120, height = 28, pad = 2;
  // 높이는 CSS로 고정(preserveAspectRatio=none). 날짜는 툴팁에 있다.
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "none", role: "img", "aria-label": metric + " 추이" });
  const values = buckets.map((b) => b.values[metric].value);
  const isCount = metric === "MET-01";
  const max = isCount ? Math.max(1, ...values.map((v) => v || 0)) : 1;
  const step = (width - pad * 2) / buckets.length;
  const y = (v) => height - pad - (v / max) * (height - pad * 2);
  svg.append(svgEl("line", { class: "chart-axis", x1: pad, x2: width - pad, y1: height - pad, y2: height - pad }));
  if (metric === "MET-02" && typeof target === "number") {
    svg.append(svgEl("line", { class: "chart-target", x1: pad, x2: width - pad, y1: y(target), y2: y(target) }));
  }
  const points = [];
  buckets.forEach((bucket, index) => {
    const cx = pad + step * index + step / 2;
    const value = values[index];
    const sample = bucket.values[metric].sample;
    const rows = [["값", formatMetric(metric, value)], ["표본", sample + "건" + (bucket.values[metric].reference_only ? " (참고)" : "")]];
    if (bucket.partial) rows.push(["상태", "진행 중"]);
    let mark;
    if (isCount) {
      const h = (height - pad * 2) * ((value || 0) / max);
      mark = svgEl("rect", { class: "chart-bar" + (bucket.partial ? " partial" : ""), x: cx - step * 0.3, y: height - pad - h, width: step * 0.6, height: Math.max(h, 0.5) });
    } else if (value !== null) {
      points.push([cx, y(value)]);
      mark = svgEl("circle", { class: "chart-dot" + (bucket.partial ? " partial" : ""), cx, cy: y(value), r: 1.6 });
    }
    const hit = svgEl("rect", { x: cx - step / 2, y: 0, width: step, height, fill: "transparent" });
    hit.addEventListener("mousemove", (event) => showTip(event, bucket.label, rows, definition));
    hit.addEventListener("mouseleave", hideTip);
    if (mark) svg.append(mark);
    svg.append(hit);
  });
  if (points.length > 1) {
    svg.insertBefore(svgEl("polyline", { class: "chart-line", points: points.map((p) => p.join(",")).join(" ") }), svg.firstChild.nextSibling);
  }
  return svg;
}
