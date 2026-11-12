// 한 화면 일괄 로그인(AUTH-01·09): 필요한 계정만 묻고, 전송 직후 입력칸을 비운다. 값은 어디에도 보관하지 않는다.
"use strict";

function loginField(id, label, type) {
  return el("div", { class: "field" }, el("label", { for: id }, label),
    el("input", { id: id, type: type, autocomplete: type === "password" ? "new-password" : "off",
      autocapitalize: "off", spellcheck: "false", maxlength: type === "password" ? "256" : "100" }));
}

function clearAllLoginInputs() {
  for (const box of document.querySelectorAll("#login-all-form input")) {
    if (box.type === "checkbox") box.checked = false; else box.value = "";
  }
}

function valueOf(id) {
  const box = document.getElementById(id);
  return box ? box.value : "";
}

async function renderLoginAll() {
  const box = document.getElementById("login-all");
  const result = await api("GET", "/api/login/pending");
  if (!result.ok) { box.replaceChildren(); return; }
  const data = result.data;
  if (data.sites.length === 0) {
    box.replaceChildren(el("div", { class: "set-row" }, el("div", {},
      el("div", { class: "k" }, "사이트 일괄 로그인"),
      el("div", { class: "s" }, "연결할 사이트가 모두 로그인돼 있습니다."))));
    return;
  }
  const labels = data.sites.map((s) => SITE_LABELS[s] || s).join(", ");
  const parts = [];
  if (data.need_company) {
    parts.push(el("div", { class: "k" }, "회사 계정 (AI-Ground·G-Knowledge)"),
      loginField("la-company-id", "회사 이메일", "text"),
      loginField("la-company-pw", "암호", "password"));
  } else if (data.sites.some((s) => COMPANY_SITES.includes(s))) {
    parts.push(el("div", { class: "s" }, "회사 계정 사이트는 기존 로그인으로 자동 연결합니다."));
  }
  if (data.need_wiki) {
    parts.push(el("div", { class: "k" }, "위키 계정"),
      loginField("la-wiki-id", "위키 ID", "text"),
      loginField("la-wiki-pw", "위키 암호", "password"));
    if (data.need_company) {
      parts.push(el("label", { class: "t-sub" },
        el("input", { type: "checkbox", id: "la-wiki-same" }), " 위키 암호가 회사 계정 암호와 같음"));
    }
  }
  box.replaceChildren(el("div", { class: "set-row" }, el("div", {},
    el("div", { class: "k" }, "사이트 일괄 로그인"),
    el("div", { class: "s" }, "로그인할 사이트: " + labels + " · 입력한 정보는 로그인에 한 번만 쓰고 바로 지웁니다(저장 안 함)"),
    el("form", { id: "login-all-form", autocomplete: "off" }, ...parts,
      el("p", { class: "s", id: "la-mfa" }),
      el("div", { class: "m-actions" }, el("button", { class: "btn primary", id: "la-submit" }, "한 번에 로그인"))))));
  document.getElementById("login-all-form").addEventListener("submit", submitLoginAll);
}

async function submitLoginAll(event) {
  event.preventDefault();
  const same = document.getElementById("la-wiki-same");
  const payload = {
    company_id: valueOf("la-company-id"), company_password: valueOf("la-company-pw"),
    wiki_id: valueOf("la-wiki-id"), wiki_password: valueOf("la-wiki-pw"),
    wiki_same_password: Boolean(same && same.checked),
  };
  clearAllLoginInputs();  // 전송 직후 화면에서 지운다
  const button = document.getElementById("la-submit");
  button.disabled = true;
  const stopMfa = watchMfa(document.getElementById("la-mfa"));
  const result = await api("POST", "/api/login/all", payload);
  stopMfa();
  for (const key of Object.keys(payload)) payload[key] = "";
  button.disabled = false;
  if (!result.ok) { toast(errorText(result, "로그인하지 못했습니다")); return; }
  const failed = result.data.results.filter((r) => !r.logged_in);
  toast(failed.length === 0 ? "모든 사이트에 로그인했습니다"
    : failed.map((r) => (SITE_LABELS[r.site] || r.site) + ": " + r.error).join(" / "));
  renderConnections();
  renderLoginAll();
}

document.addEventListener("connections", () => renderLoginAll());
