// 로그인 창(AUTH-01·04): 제출 직후 입력칸을 비우고, 값을 어디에도 보관하지 않는다.
"use strict";

let loginPending = null;  // { site, onDone }

function clearLoginInputs() {
  document.getElementById("lg-id").value = "";
  document.getElementById("lg-pw").value = "";
}

async function requireLogin(site, onDone) {
  const status = await api("GET", "/api/connections");
  const info = (status.data || []).find((item) => item.site === site);
  if (info && info.browser_login) {
    toast((SITE_LABELS[site] || site) + " 로그인 화면을 엽니다. 브라우저 창에서 직접 로그인해 주세요.");
    const result = await api("POST", "/api/login/browser", { site });
    if (!result.ok) { toast(errorText(result, "로그인하지 못했습니다.")); return; }
    renderConnections();
    if (onDone) onDone();
    return;
  }
  loginPending = { site, onDone };
  document.getElementById("lg-title").textContent = (SITE_LABELS[site] || site) + " 로그인";
  document.getElementById("lg-sub").textContent = "이 작업을 하려면 " + (SITE_LABELS[site] || site) + " 로그인이 필요합니다.";
  document.getElementById("lg-err").textContent = "";
  clearLoginInputs();
  openDlg("dlg-login");
  setTimeout(() => document.getElementById("lg-id").focus(), 50);
}

function closeLogin() {
  clearLoginInputs();
  closeDlg("dlg-login");
  loginPending = null;
}

async function submitLogin(event) {
  event.preventDefault();
  if (!loginPending) return;
  const idBox = document.getElementById("lg-id");
  const pwBox = document.getElementById("lg-pw");
  const payload = { site: loginPending.site, user_id: idBox.value, password: pwBox.value };
  clearLoginInputs();  // 전송 직후 화면에서 지운다
  const result = await api("POST", "/api/login", payload);
  payload.user_id = ""; payload.password = "";
  if (!result.ok) {
    document.getElementById("lg-err").textContent = errorText(result, "로그인하지 못했습니다.");
    return;
  }
  const done = loginPending.onDone;
  const site = loginPending.site;
  closeLogin();
  toast((SITE_LABELS[site] || site) + " 로그인됨 (세션은 메모리에만 유지)");
  renderConnections();
  if (done) done();
}

async function renderConnections() {
  const box = document.getElementById("conn");
  if (!box) return;
  const result = await api("GET", "/api/connections");
  if (!result.ok) return;
  box.replaceChildren(...result.data.map((item) => el("span",
    { class: "chip" + (item.logged_in ? " on" : "") },
    (SITE_LABELS[item.site] || item.site) + (item.logged_in ? " ●" : " ○"))));
  document.dispatchEvent(new CustomEvent("connections", { detail: result.data }));
}

document.getElementById("lg-form").addEventListener("submit", submitLogin);
document.getElementById("lg-cancel").addEventListener("click", closeLogin);
renderConnections();
