// 설정·연결: 주간보고 참고 링크 등록·삭제(WIKI-01). 서버가 https·길이·플랫폼을 다시 검증한다.
"use strict";

async function loadLinks() {
  const result = await api("GET", "/api/weekly/links");
  if (!result.ok) return;
  const { links, max_links: maxLinks } = result.data;
  document.getElementById("wl-count").textContent = links.length + " / " + maxLinks;
  document.getElementById("wl-add").disabled = links.length >= maxLinks;
  const list = document.getElementById("wl-list");
  if (!links.length) { fill(list, el("div", { class: "s" }, "등록된 링크가 없습니다. 미등록이면 기본 양식으로 초안을 만듭니다.")); return; }
  fill(list, links.map((link) => el("div", { class: "wl-item" },
    platformBadge(link.platform, link.platform_label),
    el("div", { class: "wl-main" },
      el("div", { class: "wl-name" }, link.name || "(이름 없음)", link.outline_lines ? " · 양식 목차 " + link.outline_lines + "줄" : ""),
      link.href
        ? el("a", { class: "wl-url", href: link.href, target: "_blank", rel: "noopener noreferrer" }, link.url)
        : el("span", { class: "wl-url" }, link.url)),
    el("button", { class: "btn sm", type: "button", onclick: async () => {
      const removed = await api("DELETE", "/api/weekly/links/" + link.id);
      toast(removed.ok ? "링크를 삭제했습니다" : errorText(removed, "삭제하지 못했습니다"), { kind: removed.ok ? "ok" : "error" });
      loadLinks();
    } }, "삭제"))));
}

document.getElementById("wl-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const result = await api("POST", "/api/weekly/links", {
    url: document.getElementById("wl-url").value,
    name: document.getElementById("wl-name").value,
    outline: document.getElementById("wl-outline").value,
  });
  if (!result.ok) { toast(errorText(result, "등록하지 못했습니다"), { kind: "error" }); return; }
  document.getElementById("wl-form").reset();
  toast(result.data.platform_label + " 링크로 등록했습니다");
  loadLinks();
});
loadLinks();
