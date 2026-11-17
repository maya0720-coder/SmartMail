// 주간보고 플랫폼별 복사·게시(C2, WIKI-05·06, 결정기록 0041).
// - [게시 대상 ▾] 형식 7종: 위키·위키 편집기 원본·Slack·Teams·메일·Markdown·일반 텍스트
// - 복사는 한 번에 text/html + text/plain(형식에 HTML이 없으면 text/plain만). 실패하면 선택 복사 → 수동 칸
// - [복사 미리보기]: Slack·Markdown·텍스트는 글자 그대로, Teams·메일·위키는 HTML(허용 노드만 다시 만듦)
// - 게시: 미리보기 확인(nonce) → 승인 → 위키만 직접 게시, 그 밖은 복사 + 대상 열기(메일은 제목만 mailto)
// 메일을 보내는 기능은 없다. 모든 글자는 el()/textContent로만 넣는다.
"use strict";

let FORMATS = [];
const FORMAT_FOR_PLATFORM = { wiki: "wiki", slack: "slack", teams: "teams", mail: "mail", markdown: "markdown", web: "text" };

const formatInfo = () => FORMATS.find((f) => f.key === document.getElementById("wk-format").value) || FORMATS[0];

async function loadFormats() {
  const result = await api("GET", "/api/weekly/formats");
  if (!result.ok) return;
  FORMATS = result.data;
  fill(document.getElementById("wk-format"), ...FORMATS.map((f) => el("option", { value: f.key }, f.label)));
  syncFormatToLink();
}

// 참고 링크를 고르면 그 플랫폼의 기본 형식으로 맞춘다
function syncFormatToLink() {
  const link = selectedLink();
  const key = FORMAT_FOR_PLATFORM[link ? link.platform : "wiki"] || "wiki";
  const select = document.getElementById("wk-format");
  if (FORMATS.some((f) => f.key === key)) select.value = key;
  updatePublishLabel();
}

function updatePublishLabel() {
  const info = formatInfo();
  if (info) document.getElementById("btn-publish").textContent = "승인 후 " + info.action;
}

async function convertFormat() {
  let draft;
  try { draft = currentDraft(); } catch (error) { toast(error.message, { kind: "error" }); return null; }
  const info = formatInfo();
  const result = await api("POST", "/api/weekly/convert", { ...draft, format: info.key });
  if (!result.ok) { toast(errorText(result, "형식을 바꾸지 못했습니다"), { kind: "error" }); return null; }
  return { info, text: result.data.text, html: result.data.html };
}

async function copyFormat() {
  const out = await convertFormat();
  if (!out) return;
  const ok = await copyText(out.text, out.html);
  if (ok) toast("클립보드에 복사했습니다(" + out.info.label + (out.html ? " · 서식+글자" : " · 글자") + ")");
  else { showManual(out.text); toast("자동 복사에 실패했습니다. 선택된 내용을 Ctrl+C로 복사해 주세요", { kind: "warn" }); }
}

async function copyPreview() {
  const out = await convertFormat();
  if (!out) return;
  document.getElementById("copyprev-head").textContent = "복사 미리보기 · " + out.info.label;
  const text = document.getElementById("copyprev-text");
  const rendered = document.getElementById("copyprev-html");
  const showHtml = Boolean(out.html) && ["wiki", "teams", "mail"].includes(out.info.key);
  text.textContent = out.text;
  text.classList.toggle("hidden", showHtml);
  if (showHtml) fill(rendered, ...richNodes(out.html)); else rendered.replaceChildren();
  rendered.classList.toggle("hidden", !showHtml);
  openDlg("dlg-copyprev");
}

// 같은 주·같은 대상에 게시한 기록이 있으면 다시 게시할지 묻는다
async function confirmRepublish(info) {
  const cache = await api("GET", "/api/weekly/cache");
  if (!cache.ok) return true;
  const same = cache.data.some((c) => c.week_start.slice(0, 10) === String(weekStart).slice(0, 10) && c.platform === info.platform);
  return !same || window.confirm("이번 주에 같은 대상(" + info.label + ")으로 이미 게시했습니다. 다시 게시할까요?");
}

// 게시 결과: 위키 직접 게시면 안내만, 그 밖은 클립보드에 넣고 대상 열기(메일은 제목만 mailto)
async function deliverShare(data) {
  if (data.method === "api") { toast("위키에 게시했습니다 (버전 " + data.version + ")"); return; }
  const ok = await copyText(data.text, data.html || null);
  if (!ok) showManual(data.text);
  const message = (ok ? "복사했습니다. " : "자동 복사에 실패했습니다. 아래 내용을 직접 복사해 ") +
    "대상에 붙여넣어 주세요" + (data.reason ? " (" + data.reason + ")" : "");
  toast(message, { kind: ok ? "ok" : "warn", ms: 8000, action: data.open_link ? "대상 열기" : null, onAction: () => openHttps(data.open_link) });
  if (typeof data.mailto === "string" && /^mailto:\?subject=[^&#]*$/.test(data.mailto)) window.location.href = data.mailto;
  else openHttps(data.open_link);
}

function initShare() {
  document.getElementById("wk-format").addEventListener("change", updatePublishLabel);
  document.getElementById("btn-copy").addEventListener("click", copyFormat);
  document.getElementById("btn-copy-preview").addEventListener("click", copyPreview);
  document.getElementById("copyprev-close").addEventListener("click", () => closeDlg("dlg-copyprev"));
  loadFormats();
}
