// 일괄 로그인 창(AUTH-01·04·05·09, 결정기록 0014·0034).
// - 앱을 열 때 자동으로 띄우지 않는다. 연동이 필요한 버튼을 누를 때 ensureLogin()이 필요한 사이트만 확인해 연다.
// - ID 1개 + 공통 암호. AI-Ground·G-Knowledge는 ID@도메인, 위키는 ID로 서버가 바꿔 쓴다.
// - 입력값은 전송 직후 화면에서 지우고, 어디에도 보관하지 않는다.
"use strict";

const COMPANY_SITES = ["ai-ground", "g-knowledge"];
let startCallbacks = [];
let startCancels = [];  // 창을 로그인 없이 닫을 때 부를 함수(필요할 때 로그인: 취소 안내)
let startSites = [];
let startOnly = null;  // 직접 로그인 모드(예: "groupware"). null이면 일괄 로그인
let startNeed = null;  // 이번 작업에 필요한 사이트 목록(null이면 로그인 안 된 일괄 대상 전부)
let startOthers = [];  // 이번 작업에는 필요 없지만 아직 로그인 안 된 일괄 대상 사이트
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
  const callbacks = success ? startCallbacks : startCancels;
  startCallbacks = [];
  startCancels = [];
  startOnly = null;
  startNeed = null;
  closeDlg("dlg-start");
  clearStartInputs(true);
  renderConnections();
  for (const done of callbacks) done();
}

const siteNames = (sites) => sites.map((s) => SITE_LABELS[s] || s).join(", ");

// 필요한 사이트 + ('다른 사이트도 함께' 체크 시) 아직 로그인 안 된 나머지
function syncStartSites() {
  if (!startNeed) return;
  const also = document.getElementById("st-also").checked;
  startSites = also ? [...startNeed, ...startOthers.filter((s) => !startNeed.includes(s))] : [...startNeed];
  document.getElementById("st-more").classList.toggle("hidden", !startSites.includes("wiki"));
  renderStartSites({});
}

// only: 그 사이트만 직접 로그인(그룹웨어는 일괄 로그인에서 빠져 있어 이 방식으로만 로그인한다)
// opts: { need: 이번 작업에 필요한 사이트, label: 작업 이름, onCancel: 로그인 없이 닫을 때 }
async function openStartLogin(onDone, only, opts) {
  const options = opts || {};
  const pending = await api("GET", "/api/login/pending" + (only ? "?site=" + encodeURIComponent(only) : ""));
  if (!pending.ok) return;
  const need = only ? null : (options.need || null);
  if (!need && !pending.data.sites.length) { if (onDone) onDone(); return; }
  if (need && !need.length) { if (onDone) onDone(); return; }
  if (onDone) startCallbacks.push(onDone);
  if (options.onCancel) startCancels.push(options.onCancel);
  startOnly = only || null;
  startNeed = need;
  startOthers = need ? pending.data.sites.filter((s) => !need.includes(s)) : [];
  startSites = pending.data.sites;
  const label = startOnly ? (SITE_LABELS[startOnly] || startOnly) : "";
  document.getElementById("st-title").textContent = startOnly ? label + " 로그인"
    : options.label ? options.label + " · 로그인이 필요합니다" : START_TEXT.title;
  document.getElementById("st-sub").textContent = startOnly
    ? label + "에 직접 로그인합니다. 인증문자·보안문자를 요구하면 이 창에서 입력합니다."
    : need ? "이번 작업에 필요한 사이트: " + siteNames(need) + ". 로그인하면 작업을 이어서 진행합니다." : START_TEXT.sub;
  const domain = "@" + pending.data.account_domain;
  document.getElementById("st-suffix").textContent = domain;
  document.getElementById("st-suffix").classList.toggle("hidden", Boolean(startOnly));  // 그룹웨어는 아이디만
  document.getElementById("st-id-hint").classList.toggle("hidden", Boolean(startOnly));
  document.getElementById("st-domain").textContent = domain;
  document.getElementById("st-more").classList.toggle("hidden", !pending.data.need_wiki);
  document.getElementById("st-also-row").classList.toggle("hidden", !(need && startOthers.length));
  document.getElementById("st-also").checked = true;  // 기본: 다른 사이트도 함께
  document.getElementById("st-err").textContent = "";
  renderStartSites({});
  syncStartSites();
  openDlg("dlg-start");
  // 이미 창 안 입력칸에 초점이 있으면 옮기지 않는다(입력 중인 암호가 아이디 칸으로 들어가지 않게)
  setTimeout(() => {
    if (!document.getElementById("dlg-start").contains(document.activeElement)) document.getElementById("st-id").focus();
  }, 50);
}

async function submitStart(event) {
  event.preventDefault();
  const payload = {
    user_id: document.getElementById("st-id").value.trim(),
    password: document.getElementById("st-pw").value,
    wiki_password: document.getElementById("st-wpw").value,
  };
  if (startOnly) payload.only = startOnly;
  else if (startNeed) payload.sites = startSites;
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
    toast(startOnly ? (SITE_LABELS[startOnly] || startOnly) + "에 로그인했습니다"
      : startNeed ? siteNames(startSites) + "에 로그인했습니다" : "연결 사이트에 모두 로그인했습니다", { kind: "ok" });
    setTimeout(() => finishStart(true), 400);
    return;
  }
  document.getElementById("st-err").textContent = failed.map((r) => (SITE_LABELS[r.site] || r.site) + ": " + r.error).join(" · ");
  renderConnections();
}

// '닫기': 로그인 없이 창을 닫는다(시작 시 자동 띄움이 없으므로 /api/login/later는 쓰지 않음, 결정기록 0034)
function laterStart() {
  finishStart(false);
}

// 연동이 필요한 작업 직전에 부른다(결정기록 0034).
// sites: 이번 작업에 필요한 사이트, action: 로그인되면 실행할 작업(이미 로그인돼 있으면 바로 실행)
// opts: { label: 창 제목에 넣을 작업 이름, force: 가짜 어댑터라 로그인이 필요 없어도 로그인 창을 연다(연결 칩) }
async function ensureLogin(sites, action, opts) {
  const options = opts || {};
  const run = () => { renderConnections(); if (action) action(); };
  const cancel = () => toast("로그인 후 다시 시도해 주세요", { kind: "warn" });
  const status = await api("GET", "/api/connections");
  if (!status.ok) { toast(errorText(status, "연결 상태를 읽지 못했습니다"), { kind: "error" }); return; }
  const info = new Map(status.data.map((item) => [item.site, item]));
  const missing = sites.filter((s) => {
    const item = info.get(s);
    return item && !item.logged_in && (options.force || item.needs_login);
  });
  if (!missing.length) { if (action) action(); return; }
  const again = () => ensureLogin(sites, action, options);
  // 사이트 로그인 화면을 직접 여는 사이트(2단계 인증·SSO 설정)
  const browserSite = missing.find((s) => info.get(s).browser_login);
  if (browserSite) { requireLogin(browserSite, again); return; }
  const pending = await api("GET", "/api/login/pending");
  if (!pending.ok) return;
  // 일괄 로그인에서 뺀 사이트(그룹웨어)는 그 사이트만 묻는 창으로 먼저 로그인한 뒤 이어서 확인한다
  const manual = missing.find((s) => (pending.data.manual_sites || []).includes(s));
  if (manual) { openStartLogin(again, manual, { onCancel: cancel }); return; }
  // 회사 계정이 이미 연결돼 있으면 입력 없이 SSO로 먼저 연결한다
  let remaining = missing;
  const company = missing.filter((s) => COMPANY_SITES.includes(s));
  if (company.length && !pending.data.need_company) {
    const sso = await api("POST", "/api/login/all", { sites: company });
    const done = new Set(sso.ok ? sso.data.results.filter((r) => r.logged_in).map((r) => r.site) : []);
    remaining = missing.filter((s) => !done.has(s));
  }
  if (!remaining.length) { run(); return; }
  openStartLogin(run, null, { need: remaining, label: options.label, onCancel: cancel });
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
    { kind: "warn", action: "다시 로그인",
      onAction: () => ensureLogin(ended.map((item) => item.site), null, { force: true, label: "다시 로그인" }) });
}

// 연결 칩(U8, 결정기록 0034): 로그인된 칩은 사이트를 새 탭으로 열고(https 주소만: el()이 검사),
// 로그인되지 않은 칩은 버튼으로 그려 누르면 그 사이트에 로그인한다.
function connectionChip(item) {
  const name = SITE_LABELS[item.site] || item.site;
  if (!item.logged_in) {
    return el("button", { type: "button", class: "chip dot chip-login", title: connectionTitle(item) + " · 누르면 로그인",
      onclick: () => ensureLogin([item.site], null, { force: true, label: name + " 연결" }) }, name);
  }
  return el(item.url ? "a" : "span", { class: "chip dot on", href: item.url || null, target: item.url ? "_blank" : null,
    rel: item.url ? "noopener noreferrer" : null, title: connectionTitle(item) }, name);
}

async function renderConnections() {
  const box = document.getElementById("conn");
  if (!box) return;
  const result = await api("GET", "/api/connections");
  if (!result.ok) return;
  // '모두 연결': 로그인 안 된 일괄 대상이 있을 때만(그룹웨어는 이번 실행에 필요할 때만 포함)
  const waiting = result.data.filter((item) => !item.logged_in && (item.site !== "groupware" || item.needs_login))
    .map((item) => item.site);
  box.replaceChildren(...result.data.map(connectionChip),
    waiting.length ? el("button", { type: "button", class: "chip conn-all", id: "conn-all", title: "로그인 안 된 사이트에 한 번에 로그인",
      onclick: () => ensureLogin(waiting, null, { force: true, label: "모두 연결" }) }, "모두 연결") : null);
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
// 창이 열려 있으면 입력칸에 초점이 오기 전에 누른 Esc도 창을 닫는다(문서 단위로 받는다)
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || !document.getElementById("dlg-start").classList.contains("show")) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  // 보안문자·인증문자 입력 중이면 그 입력만 취소한다
  if (!document.getElementById("st-captcha").classList.contains("hidden")) sendCaptcha("cancel");
  else if (!document.getElementById("st-otp").classList.contains("hidden")) sendOtp(true);
  else laterStart();
});

document.getElementById("st-also").addEventListener("change", syncStartSites);

// 앱을 열 때 로그인 창을 띄우지 않는다(결정기록 0034). 연결 상태만 그리고 주기적으로 다시 읽는다(화면이 보일 때만)
// 첫 조회는 페이지 스크립트(설정 화면 등 'connections' 이벤트를 받는 쪽)가 모두 실행된 뒤에 한다
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", renderConnections);
else renderConnections();
setInterval(() => { if (!document.hidden) renderConnections(); }, CONN_REFRESH_MS);
