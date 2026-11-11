// 공통 화면 도우미. 업무 내용·자격증명을 브라우저 저장소에 넣지 않는다.
"use strict";

const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]').content;

// 상태 변경 요청은 항상 JSON + CSRF 헤더로 보낸다.
async function api(method, url, body) {
  const options = { method, headers: { "X-CSRF-Token": CSRF_TOKEN }, credentials: "same-origin" };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  } else if (method !== "GET") {
    options.headers["Content-Type"] = "application/json";
    options.body = "{}";
  }
  const response = await fetch(url, options);
  let data = null;
  try { data = await response.json(); } catch (e) { data = null; }
  return { ok: response.ok, status: response.status, data };
}

// 텍스트 노드만 넣는 요소 생성기(HTML 문자열 해석 없음, XSS 방지).
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "href" && !/^https:\/\//i.test(String(value))) continue;  // https 링크만 허용
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function toast(message) {
  const box = document.getElementById("toast");
  box.textContent = message;
  box.style.display = "block";
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { box.style.display = "none"; }, 2600);
}

function openDlg(id) { document.getElementById(id).classList.add("show"); }
function closeDlg(id) { document.getElementById(id).classList.remove("show"); }

function errorText(result, fallback) {
  return (result.data && typeof result.data.error === "string") ? result.data.error : fallback;
}

const SITE_LABELS = { "wiki": "위키", "g-knowledge": "G-Knowledge", "ai-ground": "AI-Ground", "groupware": "그룹웨어" };
