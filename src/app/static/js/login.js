// 시작 로그인 창(AUTH-01·04·09, 결정기록 0014).
// - 앱을 열 때 로그인되지 않은 연결 사이트가 있으면 자동으로 띄운다('나중에'를 누르면 이번 실행 동안 다시 띄우지 않음).
// - ID 1개 + 공통 암호. AI-Ground·G-Knowledge는 ID@도메인, 위키는 ID로 서버가 바꿔 쓴다.
// - 입력값은 전송 직후 화면에서 지우고, 어디에도 보관하지 않는다.
"use strict";

const COMPANY_SITES = ["ai-ground", "g-knowledge"];
let startCallbacks = [];
let startSites = [];

// 로그인이 끝날 때까지 2단계 인증 안내(Authenticator 번호)를 보여 준다. 반환값을 호출하면 멈춘다.
function watchMfa(target) {
  let stopped = false;
  async function tick() {
    if (stopped) return;
    const result = await api("GET", "/api/login/progress");
    if (!stopped && result.ok) {
      const data = result.data;
      target.textContent = !data.mfa_waiting ? ""
        : data.mfa_number ? "휴대폰 Authenticator 앱에서 번호 " + data.mfa_number + " 을(를) 눌러 승인해 주세요."
        : "휴대폰에서 로그인 요청을 승인해 주세요.";
    }
    if (!stopped) setTimeout(tick, 1000);
  }
  tick();
  return () => { stopped = true; target.textContent = ""; };
}

function clearStartInputs(all) {
  document.getElementById("st-pw").value = "";
  document.getElementById("st-wpw").value = "";
  if (all) document.getElementById("st-id").value = "";
}

// 사이트 칩: 대기 · 연결 중 · 완료 · 실패
function renderStartSites(states) {
  fill(document.getElementById("st-sites"), ...startSites.map((site) => {
    const state = states[site] || { kind: "wait" };
    const label = { wait: "대기", busy: "연결 중…", ok: "완료", fail: state.error || "실패" }[state.kind];
    const ic = { ok: icon("check"), fail: icon("x-circle") }[state.kind] || null;
    return el("li", { class: "lm-site s-" + state.kind }, el("span", { class: "nm" }, SITE_LABELS[site] || site), ic, el("span", { class: "st" }, label));
  }));
}

function finishStart(success) {
  const callbacks = startCallbacks;
  startCallbacks = [];
  closeDlg("dlg-start");
  clearStartInputs(true);
  renderConnections();
  if (success) for (const done of callbacks) done();
}

async function openStartLogin(onDone) {
  const pending = await api("GET", "/api/login/pending");
  if (!pending.ok) return;
  if (!pending.data.sites.length) { if (onDone) onDone(); return; }
  if (onDone) startCallbacks.push(onDone);
  startSites = pending.data.sites;
  const domain = "@" + pending.data.account_domain;
  document.getElementById("st-suffix").textContent = domain;
  document.getElementById("st-domain").textContent = domain;
  document.getElementById("st-more").classList.toggle("hidden", !pending.data.need_wiki);
  document.getElementById("st-err").textContent = "";
  renderStartSites({});
  openDlg("dlg-start");
  setTimeout(() => document.getElementById("st-id").focus(), 50);
}

async function submitStart(event) {
  event.preventDefault();
  const payload = {
    user_id: document.getElementById("st-id").value.trim(),
    password: document.getElementById("st-pw").value,
    wiki_password: document.getElementById("st-wpw").value,
  };
  clearStartInputs(false);  // 암호는 전송 직후 화면에서 지운다
  const button = document.getElementById("st-submit");
  button.disabled = true;
  document.getElementById("st-err").textContent = "";
  renderStartSites(Object.fromEntries(startSites.map((s) => [s, { kind: "busy" }])));
  const stopMfa = watchMfa(document.getElementById("st-mfa"));
  const result = await api("POST", "/api/login/all", payload);
  stopMfa();
  payload.password = ""; payload.wiki_password = "";
  button.disabled = false;
  if (!result.ok) { document.getElementById("st-err").textContent = errorText(result, "로그인하지 못했습니다."); renderStartSites({}); return; }
  const states = {};
  for (const r of result.data.results) states[r.site] = r.logged_in ? { kind: "ok" } : { kind: "fail", error: r.error };
  renderStartSites(states);
  const failed = result.data.results.filter((r) => !r.logged_in);
  if (!failed.length) {
    toast("연결 사이트에 모두 로그인했습니다", { kind: "ok" });
    setTimeout(() => finishStart(true), 400);
    return;
  }
  document.getElementById("st-err").textContent = failed.map((r) => (SITE_LABELS[r.site] || r.site) + ": " + r.error).join(" · ");
  renderConnections();
}

async function laterStart() {
  await api("POST", "/api/login/later");
  finishStart(false);
}

// 다른 화면에서 로그인이 필요할 때: 회사 계정은 먼저 SSO로 연결하고, 안 되면 시작 로그인 창을 연다.
async function requireLogin(site, onDone) {
  const pending = await api("GET", "/api/login/pending");
  if (pending.ok && pending.data.sites.includes(site)) {
    if (COMPANY_SITES.includes(site) && !pending.data.need_company) {
      const sso = await api("POST", "/api/login/all", { only: site });
      const mine = sso.ok ? sso.data.results.find((r) => r.site === site) : null;
      if (mine && mine.logged_in) { renderConnections(); if (onDone) onDone(); return; }
    }
    openStartLogin(onDone);
    return;
  }
  const status = await api("GET", "/api/connections");
  const info = (status.data || []).find((item) => item.site === site);
  if (info && info.logged_in) { if (onDone) onDone(); return; }
  if (info && info.browser_login) {
    toast((SITE_LABELS[site] || site) + " 로그인 화면을 엽니다. 브라우저 창에서 직접 로그인해 주세요.");
    const result = await api("POST", "/api/login/browser", { site });
    if (!result.ok) { toast(errorText(result, "로그인하지 못했습니다."), { kind: "error" }); return; }
    renderConnections();
    if (onDone) onDone();
    return;
  }
  toast((SITE_LABELS[site] || site) + " 로그인 연동은 아직 준비되지 않았습니다.", { kind: "warn" });
}

async function renderConnections() {
  const box = document.getElementById("conn");
  if (!box) return;
  const result = await api("GET", "/api/connections");
  if (!result.ok) return;
  box.replaceChildren(...result.data.map((item) => el("span",
    { class: "chip dot" + (item.logged_in ? " on" : ""), title: item.logged_in ? "로그인됨" : "로그인 안 됨" },
    SITE_LABELS[item.site] || item.site)));
  document.dispatchEvent(new CustomEvent("connections", { detail: result.data }));
}

document.getElementById("st-form").addEventListener("submit", submitStart);
document.getElementById("st-later").addEventListener("click", laterStart);
document.getElementById("dlg-start").addEventListener("keydown", (event) => {
  if (event.key === "Escape") { event.preventDefault(); laterStart(); }
});

// 앱을 열 때 자동으로 띄운다(로그인할 사이트가 있고, 이번 실행에서 '나중에'를 누르지 않았을 때)
(async () => {
  await renderConnections();
  const pending = await api("GET", "/api/login/pending");
  if (pending.ok && pending.data.sites.length && !pending.data.dismissed) openStartLogin();
})();
