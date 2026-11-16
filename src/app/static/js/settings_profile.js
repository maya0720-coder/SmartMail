// 설정·연결: 주간보고 조직 양식 프로필 등록·칸 연결(N4, WIKI-02, 결정기록 0037).
// - 위키 링크: 위키 로그인 세션으로 페이지를 읽는다(필요할 때만 로그인 창, 결정기록 0034).
// - 붙여넣기: 클립보드의 HTML(text/html)을 화면에 그리지 않고 문자열로만 서버에 보낸다(XSS 방지).
// - 서버는 구조(머리글·열 이름·고정 문구)만 저장한다. 칸 연결은 자동 제안을 사용자가 고친다.
"use strict";

const PF = { link: null, pasted: "", data: null };
const PF_KIND = { heading: "머리글", table: "표", list: "목록", paragraph: "고정 문구" };
const PF_MAX_BYTES = 500000;

function pfButton(label, onclick) {
  return el("button", { class: "btn sm", type: "button", onclick }, label);
}

// settings_links.js가 링크 줄마다 부른다
function profileButtons(link) {
  const out = [];
  if (link.has_profile) out.push(el("span", { class: "chip" }, "양식 등록됨"), pfButton("칸 연결", () => openBindings(link)));
  if (link.platform === "wiki") out.push(pfButton("위키에서 양식 읽기", () => ensureLogin(["wiki"], () => importWiki(link), { label: "위키 양식 읽기" })));
  out.push(pfButton("양식 붙여넣기", () => openPaste(link)));
  if (link.has_profile) out.push(pfButton("양식 삭제", () => deleteProfile(link)));
  return out;
}

async function importWiki(link) {
  const result = await api("POST", "/api/weekly/links/" + link.id + "/profile/import");
  if (!result.ok) {
    if (result.status === 503 && result.data && result.data.code === "login_required") {
      ensureLogin(["wiki"], () => importWiki(link), { force: true, label: "위키 양식 읽기" });
      return;
    }
    toast(errorText(result, "양식을 읽지 못했습니다"), { kind: "error" });
    return;
  }
  toast("위키 양식을 읽었습니다 · 칸 연결을 확인해 주세요");
  loadLinks();
  showBindings(link, result.data);
}

// ---- 붙여넣기 ----
function openPaste(link) {
  PF.link = link;
  PF.pasted = "";
  document.getElementById("pf-paste-name").textContent = link.name || link.url;
  document.getElementById("pf-paste-info").textContent = "아직 붙여넣지 않았습니다";
  document.getElementById("pf-paste-ok").disabled = true;
  openDlg("dlg-pf-paste");
  document.getElementById("pf-paste").focus();
}

document.getElementById("pf-paste").addEventListener("paste", (event) => {
  event.preventDefault();  // 붙여넣은 HTML은 화면에 그리지 않는다
  const html = (event.clipboardData && event.clipboardData.getData("text/html")) || "";
  const info = document.getElementById("pf-paste-info");
  if (!html) { PF.pasted = ""; info.textContent = "서식이 있는 내용이 아닙니다. 위키 페이지에서 내 영역을 선택해 복사해 주세요"; return; }
  const size = new TextEncoder().encode(html).length;
  if (size > PF_MAX_BYTES) { PF.pasted = ""; info.textContent = "너무 큽니다(500KB 이하)"; return; }
  PF.pasted = html;
  info.textContent = "붙여넣음 " + Math.max(1, Math.round(size / 1024)) + "KB · 등록하면 구조만 저장합니다";
  document.getElementById("pf-paste-ok").disabled = false;
});
document.getElementById("pf-paste").addEventListener("drop", (event) => event.preventDefault());
document.getElementById("pf-paste").addEventListener("beforeinput", (event) => event.preventDefault());

document.getElementById("pf-paste-ok").addEventListener("click", async () => {
  if (!PF.pasted || !PF.link) return;
  const link = PF.link;
  const result = await api("POST", "/api/weekly/links/" + link.id + "/profile/paste", { html: PF.pasted });
  PF.pasted = "";  // 원본 HTML은 메모리에서도 바로 지운다
  if (!result.ok) { toast(errorText(result, "양식을 등록하지 못했습니다"), { kind: "error" }); return; }
  closeDlg("dlg-pf-paste");
  toast("양식을 등록했습니다 · 칸 연결을 확인해 주세요");
  loadLinks();
  showBindings(link, result.data);
});
document.getElementById("pf-paste-cancel").addEventListener("click", () => { PF.pasted = ""; closeDlg("dlg-pf-paste"); });

// ---- 칸 연결 ----
async function openBindings(link) {
  const result = await api("GET", "/api/weekly/links/" + link.id + "/profile");
  if (!result.ok || !result.data.profile) { toast(errorText(result, "등록된 양식이 없습니다"), { kind: "warn" }); return; }
  showBindings(link, result.data);
}

function pfSelect(options, value, label, data) {
  return el("select", { class: "inline-input", "aria-label": label, dataset: data },
    ...Object.entries(options).map(([key, text]) => el("option", { value: key, selected: key === value }, text)));
}

function showBindings(link, data) {
  PF.link = link;
  PF.data = data;
  const profile = data.profile;
  document.getElementById("pf-name").textContent = (link.name || link.url) + " · " + (profile.source === "wiki" ? "위키에서 읽음" : "붙여넣기");
  fill(document.getElementById("pf-blocks"), ...profile.blocks.map((block, index) => {
    const key = String(index);
    let body;
    if (block.kind === "heading") {
      body = el("div", { class: "actions tight" }, el("b", {}, block.text),
        pfSelect(data.sources, profile.bindings.headings[key] || "none", block.text + " 넣을 데이터", { head: key }));
    } else if (block.kind === "table") {
      const fields = profile.bindings.columns[key] || [];
      body = el("div", { class: "pf-cols" }, ...block.columns.map((name, col) => el("label", {}, name,
        pfSelect(data.fields, fields[col] || "note", name + " 열 필드", { table: key, col: String(col) }))));
    } else {
      body = el("span", { class: "t-sub" }, block.kind === "list" ? (block.style === "ol" ? "번호 목록" : "글머리 목록") : block.text);
    }
    return el("div", { class: "pf-block" }, el("span", { class: "pf-kind" }, PF_KIND[block.kind] + (block.level ? " " + block.level : "")), body);
  }));
  openDlg("dlg-pf");
}

document.getElementById("pf-save").addEventListener("click", async () => {
  if (!PF.link || !PF.data) return;
  const headings = {};
  const columns = {};
  for (const select of document.querySelectorAll("#pf-blocks select")) {
    if (select.dataset.head) headings[select.dataset.head] = select.value;
    else (columns[select.dataset.table] = columns[select.dataset.table] || [])[Number(select.dataset.col)] = select.value;
  }
  const result = await api("PUT", "/api/weekly/links/" + PF.link.id + "/profile/bindings", { headings, columns });
  if (!result.ok) { toast(errorText(result, "칸 연결을 저장하지 못했습니다"), { kind: "error" }); return; }
  closeDlg("dlg-pf");
  toast("칸 연결을 저장했습니다");
});
document.getElementById("pf-cancel").addEventListener("click", () => closeDlg("dlg-pf"));

async function deleteProfile(link) {
  if (!window.confirm("이 링크의 양식 구조와 칸 연결을 지울까요?")) return;
  const result = await api("DELETE", "/api/weekly/links/" + link.id + "/profile");
  toast(result.ok ? "양식을 지웠습니다" : errorText(result, "지우지 못했습니다"), { kind: result.ok ? "ok" : "error" });
  loadLinks();
}
