// 주간보고 공용: 플랫폼 배지, 클립보드 복사(설정·주간보고 화면 공통). 모든 글자는 el()/textContent로만 넣는다.
"use strict";

const PLATFORM_MARK = { slack: "S", wiki: "W", web: "↗", teams: "T", mail: "M", markdown: "#" };

function platformBadge(platform, label) {
  const mark = PLATFORM_MARK[platform] || PLATFORM_MARK.web;
  return el("span", { class: "pf " + (PLATFORM_MARK[platform] ? platform : "web"), title: label },
    el("span", { class: "pf-ic", "aria-hidden": "true" }, mark), label);
}

// 선택 영역 폴백: 화면 밖 textarea를 선택해 복사 명령을 쓴다.
function selectionCopy(text) {
  const buffer = el("textarea", { class: "wk-copybuf", "aria-hidden": "true", readonly: true });
  buffer.value = text;
  document.body.append(buffer);
  buffer.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  buffer.remove();
  return ok;
}

// 클립보드 복사. html을 주면 text/html(위키 편집기 붙여넣기용)과 text/plain을 함께 넣는다.
// 실패하면 선택 영역 복사로 한 번 더 시도하고, 그것도 안 되면 false(호출한 쪽이 수동 복사 안내).
async function copyText(text, html) {
  try {
    if (html && window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
      await navigator.clipboard.write([new ClipboardItem({
        "text/plain": new Blob([text], { type: "text/plain" }),
        "text/html": new Blob([html], { type: "text/html" }),
      })]);
      return true;
    }
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    return selectionCopy(text);
  }
}

// 새 창 열기: https만, opener 차단.
function openHttps(url) {
  if (typeof url !== "string" || !/^https:\/\//i.test(url)) return;
  window.open(url, "_blank", "noopener,noreferrer");
}
