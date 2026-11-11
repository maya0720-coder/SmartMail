// 설정·연결 화면: 사이트별 로그인 상태, 로그아웃, 전체 삭제.
"use strict";

function renderSiteRows(items) {
  const box = document.getElementById("setconn");
  box.replaceChildren(...items.map((item) => {
    const label = SITE_LABELS[item.site] || item.site;
    const action = item.logged_in
      ? el("button", { class: "btn sm", onclick: async () => {
          await api("POST", "/api/logout", { site: item.site });
          toast(label + " 로그아웃됨"); renderConnections();
        } }, "로그아웃")
      : el("button", { class: "btn sm primary", onclick: () => requireLogin(item.site) }, "로그인");
    return el("div", { class: "set-row" },
      el("div", {}, el("div", { class: "k" }, label),
        el("div", { class: "s" }, item.logged_in ? "로그인됨 (세션은 메모리에만 유지)" : "로그인 안 됨",
          item.browser_login ? " · 사이트 로그인 화면 사용(2단계 인증·SSO)" : "")),
      action);
  }));
}

document.addEventListener("connections", (event) => renderSiteRows(event.detail));

document.getElementById("btn-logout-all").addEventListener("click", async () => {
  const result = await api("POST", "/api/logout-all");
  toast(result.ok ? "모든 사이트에서 로그아웃했습니다" : errorText(result, "처리하지 못했습니다"));
  renderConnections();
});

document.getElementById("btn-wipe").addEventListener("click", async () => {
  if (!window.confirm("로컬 데이터를 모두 지울까요? 되돌릴 수 없습니다.")) return;
  const result = await api("POST", "/api/data/wipe", { confirm: true });
  toast(result.ok ? "로컬 데이터를 모두 지웠습니다" : errorText(result, "삭제하지 못했습니다"));
});

document.getElementById("btn-detect").addEventListener("click", async () => {
  const result = await api("POST", "/api/mail/detect");
  if (result.ok) document.getElementById("collect-desc").textContent = result.data.message;
  else toast(errorText(result, "확인하지 못했습니다"));
});
