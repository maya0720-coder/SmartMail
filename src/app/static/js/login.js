// 시작 로그인 창(AUTH-01·04·09, 결정기록 0014).
// - 앱을 열 때 로그인되지 않은 연결 사이트가 있으면 자동으로 띄운다('나중에'를 누르면 이번 실행 동안 다시 띄우지 않음).
// - ID 1개 + 공통 암호. AI-Ground·G-Knowledge는 ID@도메인, 위키는 ID로 서버가 바꿔 쓴다.
// - 입력값은 전송 직후 화면에서 지우고, 어디에도 보관하지 않는다.
"use strict";

const COMPANY_SITES = ["ai-ground", "g-knowledge"];
let startCallbacks = [];
let startSites = [];
let startOnly = null;  // 직접 로그인 모드(예: "groupware"). null이면 일괄 로그인
// 일괄 로그인 창 문구(직접 로그인 모드에서 바꿨다가 되돌린다)
const START_TEXT = { title: "업무 처리 현황 시작하기", sub: "회사 계정으로 연결 사이트에 한 번에 로그인합니다." };

// 그룹웨어 인증문자 입력칸(결정기록 0020). 서버가 번호를 요청할 때만 보인다.
function showOtp(waiting, retry) {
  const box = document.getElementById("st-otp");
  const input = document.getElementById("st-otp-code");
  if (!waiting) { box.classList.add("hidden"); input.value = ""; return; }
  document.getElementById("st-otp-msg").textContent = retry
    ? "번호가 맞지 않거나 확인되지 않았습니다. 인증문자 번호를 다시 입력해 주세요."
    : "그룹웨어 인증문자로 받은 번호를 입력해 주세요.";
  if (box.classList.contains("hidden")) {
    box.classList.remove("hidden");
    setTimeout(() => input.focus(), 50);
  }
}

async function sendOtp(cancel) {
  const input = document.getElementById("st-otp-code");
  const code = input.value.trim();
  if (!cancel && !code) { input.focus(); return; }
  input.value = "";  // 번호는 전송 직후 화면에서 지운다
  showOtp(false);
  const result = await api("POST", "/api/login/otp", cancel ? { cancel: true } : { code });
  if (!result.ok) toast(errorText(result, "번호를 전달하지 못했습니다."), { kind: "error" });
}

// 그룹웨어 보안문자(결정기록 0025). 이미지는 서버가 로그인 중인 화면에서 캡처한 data URL만 쓴다.
const CAPTCHA_IMG_PREFIX = "data:image/png;base64,";
let captchaSeq = 0;

function hideCaptcha() {
  document.getElementById("st-captcha").classList.add("hidden");
  document.getElementById("st-captcha-code").value = "";
  document.getElementById("st-captcha-img").removeAttribute("src");
  captchaSeq = 0;
}

async function showCaptcha(data) {
  if (!data.captcha_waiting) { hideCaptcha(); return; }
  if (data.captcha_seq === captchaSeq) return;  // 같은 이미지는 다시 가져오지 않는다(서버가 한 번만 줌)
  const previous = captchaSeq;
  captchaSeq = data.captcha_seq;
  const result = await api("POST", "/api/login/captcha/image", {});
  const src = result.ok && result.data ? result.data.image : "";
  if (typeof src !== "string" || !src.startsWith(CAPTCHA_IMG_PREFIX)) {
    captchaSeq = previous;  // 가져오지 못했으면 다음 확인 때 다시 요청한다
    return;
  }
  document.getElementById("st-captcha-img").src = src;
  document.getElementById("st-captcha-msg").textContent = data.captcha_retry
    ? "보안문자가 맞지 않았습니다. 새 이미지의 보안문자를 다시 입력해 주세요."
    : "그룹웨어 보안문자를 입력해 주세요. 보이는 그대로 입력합니다.";
  const box = document.getElementById("st-captcha");
  const input = document.getElementById("st-captcha-code");
  input.value = "";
  box.classList.remove("hidden");
  setTimeout(() => input.focus(), 50);
}

async function sendCaptcha(action) {
  const input = document.getElementById("st-captcha-code");
  const code = input.value.trim();
  if (action === "send" && !code) { input.focus(); return; }
  input.value = "";  // 값은 전송 직후 화면에서 지운다
  const body = action === "cancel" ? { cancel: true } : action === "refresh" ? { refresh: true } : { code };
  if (action !== "refresh") hideCaptcha();
  const result = await api("POST", "/api/login/captcha", body);
  if (!result.ok) toast(errorText(result, "보안문자를 전달하지 못했습니다."), { kind: "error" });
}

// 로그인이 끝날 때까지 2단계 인증 안내(Authenticator 번호·인증문자 입력)를 보여 준다. 반환값을 호출하면 멈춘다.
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
      showOtp(data.otp_waiting, data.otp_retry);
      showCaptcha(data);
    }
    if (!stopped) setTimeout(tick, 1000);
  }
  tick();
  return () => { stopped = true; target.textContent = ""; showOtp(false); hideCaptcha(); };
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
  startOnly = null;
  closeDlg("dlg-start");
  clearStartInputs(true);
  renderConnections();
  if (success) for (const done of callbacks) done();
}

// only: 그 사이트만 직접 로그인(그룹웨어는 일괄 로그인에서 빠져 있어 이 방식으로만 로그인한다)
async function openStartLogin(onDone, only) {
  const pending = await api("GET", "/api/login/pending" + (only ? "?site=" + encodeURIComponent(only) : ""));
  if (!pending.ok) return;
  if (!pending.data.sites.length) { if (onDone) onDone(); return; }
  if (onDone) startCallbacks.push(onDone);
  startSites = pending.data.sites;
  startOnly = only || null;
  const label = startOnly ? (SITE_LABELS[startOnly] || startOnly) : "";
  document.getElementById("st-title").textContent = startOnly ? label + " 로그인" : START_TEXT.title;
  document.getElementById("st-sub").textContent = startOnly
    ? label + "에 직접 로그인합니다. 인증문자·보안문자를 요구하면 이 창에서 입력합니다." : START_TEXT.sub;
  const domain = "@" + pending.data.account_domain;
  document.getElementById("st-suffix").textContent = domain;
  document.getElementById("st-suffix").classList.toggle("hidden", Boolean(startOnly));  // 그룹웨어는 아이디만
  document.getElementById("st-id-hint").classList.toggle("hidden", Boolean(startOnly));
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
  if (startOnly) payload.only = startOnly;
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
    toast(startOnly ? (SITE_LABELS[startOnly] || startOnly) + "에 로그인했습니다" : "연결 사이트에 모두 로그인했습니다", { kind: "ok" });
    setTimeout(() => finishStart(true), 400);
    return;
  }
  document.getElementById("st-err").textContent = failed.map((r) => (SITE_LABELS[r.site] || r.site) + ": " + r.error).join(" · ");
  renderConnections();
}

async function laterStart() {
  // 직접 로그인 창을 닫는 것은 '나중에'(시작 창 자동 띄움 끄기)가 아니다
  if (!startOnly) await api("POST", "/api/login/later");
  finishStart(false);
}

// 다른 화면에서 로그인이 필요할 때: 회사 계정은 먼저 SSO로 연결하고, 안 되면 시작 로그인 창을 연다.
async function requireLogin(site, onDone) {
  const pending = await api("GET", "/api/login/pending");
  // 그룹웨어 등 일괄 로그인에서 뺀 사이트: 그 사이트만 직접 로그인하는 창을 연다
  if (pending.ok && (pending.data.manual_sites || []).includes(site)) { openStartLogin(onDone, site); return; }
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

// 연결이 끝난 사유(결정기록 0031). 서버가 정한 코드 값만 화면 문구로 바꾼다
const ENDED_LABELS = {
  site_expired: "사이트에서 로그인이 끝남",
  cascade: "함께 쓰던 회사 계정 연결이 끝남",
  idle_timeout: "오래 쓰지 않아 로그아웃됨",
  replaced: "다시 로그인함",
  logout: "로그아웃함",
  app_stop: "앱 종료",
};
// 사용자가 하지 않은 종료: 알림을 띄워 다시 로그인을 권한다
const UNEXPECTED_END = new Set(["site_expired", "cascade", "idle_timeout"]);
const CONN_REFRESH_MS = 60000;
let lastConnections = null;

function connectionTitle(item) {
  if (item.logged_in) {
    return "로그인됨" + (item.keep_while_running ? " · 유지 중(앱 실행 동안)" : "")
      + (item.url ? " · 누르면 사이트 열기" : "");
  }
  const reason = ENDED_LABELS[item.ended_reason];
  return "로그인 안 됨" + (reason ? " · " + reason : "") + (item.url ? " · 누르면 사이트 열기" : "");
}

function noticeEnded(items) {
  const before = lastConnections;
  lastConnections = new Map(items.map((item) => [item.site, item.logged_in]));
  if (!before) return;
  const ended = items.filter((item) => before.get(item.site) && !item.logged_in
    && UNEXPECTED_END.has(item.ended_reason));
  if (!ended.length) return;
  const first = ended[0];
  const names = ended.map((item) => SITE_LABELS[item.site] || item.site).join(", ");
  toast(names + ": " + ENDED_LABELS[first.ended_reason] + " · 다시 로그인해 주세요",
    { kind: "warn", action: "다시 로그인", onAction: () => openStartLogin(null) });
}

async function renderConnections() {
  const box = document.getElementById("conn");
  if (!box) return;
  const result = await api("GET", "/api/connections");
  if (!result.ok) return;
  // 상태 칩을 누르면 해당 사이트를 새 탭으로 연다(https 주소만: el()이 검사한다)
  box.replaceChildren(...result.data.map((item) => el(item.url ? "a" : "span",
    { class: "chip dot" + (item.logged_in ? " on" : ""), href: item.url || null, target: item.url ? "_blank" : null,
      rel: item.url ? "noopener noreferrer" : null, title: connectionTitle(item) },
    SITE_LABELS[item.site] || item.site)));
  noticeEnded(result.data);
  document.dispatchEvent(new CustomEvent("connections", { detail: result.data }));
}

document.getElementById("st-form").addEventListener("submit", submitStart);
document.getElementById("st-later").addEventListener("click", laterStart);
document.getElementById("st-otp-ok").addEventListener("click", () => sendOtp(false));
document.getElementById("st-otp-cancel").addEventListener("click", () => sendOtp(true));
document.getElementById("st-otp-code").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); sendOtp(false); }
});
document.getElementById("st-captcha-ok").addEventListener("click", () => sendCaptcha("send"));
document.getElementById("st-captcha-cancel").addEventListener("click", () => sendCaptcha("cancel"));
document.getElementById("st-captcha-refresh").addEventListener("click", () => sendCaptcha("refresh"));
document.getElementById("st-captcha-code").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); sendCaptcha("send"); }
});
document.getElementById("dlg-start").addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  event.preventDefault();
  // 보안문자·인증문자 입력 중이면 그 입력만 취소한다
  if (!document.getElementById("st-captcha").classList.contains("hidden")) sendCaptcha("cancel");
  else if (!document.getElementById("st-otp").classList.contains("hidden")) sendOtp(true);
  else laterStart();
});

// 앱을 열 때 자동으로 띄운다(로그인할 사이트가 있고, 이번 실행에서 '나중에'를 누르지 않았을 때)
(async () => {
  await renderConnections();
  const pending = await api("GET", "/api/login/pending");
  if (pending.ok && pending.data.sites.length && !pending.data.dismissed) openStartLogin();
  // 사이트 쪽 만료·연쇄 종료를 알 수 있게 연결 상태를 주기적으로 다시 읽는다(화면이 보일 때만)
  setInterval(() => { if (!document.hidden) renderConnections(); }, CONN_REFRESH_MS);
})();
