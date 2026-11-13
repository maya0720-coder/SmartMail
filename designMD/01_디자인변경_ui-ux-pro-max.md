# 디자인 변경 명세 — ui-ux-pro-max 버전

> 대상: SmartMail(업무 처리 관리 현황, 로컬 테스트 버전) 화면 레이어
> 작성일: 2026-11-12 · 상태: 제안(사용자 확인 전) · 비교 시안: `D:\00AI_Workspace\ui-patterns\skill-samples\02_ui-ux-pro-max.html`

## 0. 개요

| 항목 | 내용 |
|---|---|
| 목적 | 업무 대시보드의 정보 밀도와 조작 효율을 높인다. 한 화면에 더 많은 업무를 보여주고 검색·정렬·일괄 처리를 빠르게 한다. |
| 디자인 기준 | ui-ux-pro-max 스킬의 **Data-Dense Dashboard (SaaS)** 스타일 |
| 변경 범위 | `src/app/templates/`, `src/app/static/css/app.css`, `src/app/static/js/`(화면 스크립트만), 신규 정적 리소스(폰트·아이콘) |
| 변경하지 않는 것 | API·도메인·저장소·보안 미들웨어(`web_security.py`)·요구사항 동작. 화면 표현만 바꾼다. |
| 핵심 변화 | 상단 떠 있는 내비 바, 촘촘한 표(행 40px), 상태 분포 도넛, 열 정렬·검색 단축키, **내 할 일 화면 높이 기준 페이징** |

이 문서는 Claude Code가 `CLAUDE.md`와 `dev/00_MAIN_개발진행.md` 절차(계획 → 구현 → 검증 → 한글 보고)에 따라 실행하는 작업 지시서다.

## 1. 반드시 지킬 제약 (현재 코드에서 확인)

| 제약 | 근거 | 이 버전에서의 처리 |
|---|---|---|
| CSP `default-src 'self'`, `style-src 'self'`, `font-src 'self'`, 외부 CDN 금지 | `web_security.py` CSP, `test_no_external_resources` | 폰트·아이콘은 `src/app/static/`에 로컬 번들로 둔다. Google Fonts·Iconify CDN 사용 금지 |
| 템플릿 인라인 `<script>`·`style=`·`on*=` 금지 | `test_templates_have_no_inline_script_style_or_handlers` | 모든 스타일은 `app.css`, 이벤트는 JS `addEventListener`로만 연결 |
| `innerHTML`·`insertAdjacentHTML`·`localStorage` 등 금지 | `test_scripts_do_not_use_unsafe_sinks_or_storage` | 아이콘도 `createElementNS` + `<use>`로 그린다. 정렬·페이지 상태는 메모리 변수에만 둔다 |
| 기존 화면 테스트 선택자 유지 | `test_ui_browser.py` | `#todo tr`, `#seg button[data-g]`, `#mk .kpi`(4개), `#filters`, `#detail` id·구조를 유지한다 |
| 출력 인코딩 | `common.js`의 `el()` | 모든 텍스트는 `el()`(textContent)로만 넣는다 |
| 브라우저 저장소 금지 | `dev/12` 작업 4 | 정렬·필터·페이지 상태를 저장하지 않는다(새로고침 시 초기화) |

## 2. 디자인 시스템 (ui-ux-pro-max 출력 형식 기준)

### 2-1. 색 토큰 (`app.css` `:root` 교체)

| 토큰 | 라이트 | 다크 | 용도 |
|---|---|---|---|
| `--bg` | `#f8fafc` | `#0b1120` | 페이지 배경 |
| `--card` | `#ffffff` | `#111827` | 카드 |
| `--soft` | `#f1f5f9` | `#1e293b` | 보조 배경·칩 |
| `--line` | `#e2e8f0` | `#273449` | 테두리·구분선 |
| `--fg` | `#0f172a` | `#e2e8f0` | 본문 (대비 4.5:1 이상) |
| `--sub` | `#475569` | `#94a3b8` | 보조 글자 |
| `--muted` | `#94a3b8` | `#64748b` | 비활성·안내 |
| `--accent` | `#2563eb` | `#3b82f6` | Primary |
| `--accent-soft` | `#eff6ff` | `#172554` | 선택 행·배지 |
| `--cta` | `#f97316` | `#fb923c` | 주요 행동(주간보고 초안) |
| `--warn` / `--danger` / `--success` | `#d97706` / `#dc2626` / `#16a34a` | `#f59e0b` / `#f87171` / `#4ade80` | 상태 |
| `--ai` / `--gk` | 기존 값 유지 | 기존 값 유지 | AI·G-Knowledge 구분색 |

### 2-2. 타이포그래피

| 역할 | 글꼴 | 비고 |
|---|---|---|
| 본문·제목 | Pretendard (로컬 woff2) | 카탈로그 기본 조합(Fira Sans)은 한글 미지원이라 한글은 Pretendard로 대체 |
| 숫자·코드(지표 값, 기한, 건수, 단축키) | Fira Code (로컬 woff2) | `font-variant-numeric: tabular-nums` |
| 크기 | 본문 13px, 표 13px, 제목 h2 20px / h3 14px, KPI 값 22px | 밀도 우선 |

- 폰트 파일 위치: `src/app/static/fonts/` (Pretendard·Fira Code 모두 SIL OFL 1.1 → `src/app/static/fonts/LICENSE-*.txt` 동봉).
- `--font` 스택: `"Pretendard","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif`.

### 2-3. 아이콘

- 규칙: 이모지 금지, SVG 아이콘만, 한 세트로 통일. **Lucide**(ISC 라이선스)를 쓴다.
- 구현: 필요한 아이콘만 모은 SVG 스프라이트 `src/app/static/img/icons.svg`(`<symbol id="i-...">`).
- 사용:
  - 템플릿: `<svg class="ic" aria-hidden="true"><use href="/static/img/icons.svg#i-search"></use></svg>`
  - JS: `svgEl("svg",{class:"ic"})` 안에 `svgEl("use",{href:...})`를 넣는다. `charts.js`의 `svgEl`을 `common.js`로 옮겨 공용화한다.
- 사용할 아이콘: layout-grid, inbox, file-text, history, settings, search, pen-line, check, arrow-up-down, mail, target, book-open, repeat, x.

### 2-4. 간격·모양·모션

| 항목 | 값 |
|---|---|
| 모서리 | 카드 12px, 버튼·입력 8px, 배지 999px |
| 카드 | `border:1px solid var(--line)`, 그림자 없음(떠 있는 내비만 `0 4px 16px rgba(15,23,42,.06)`) |
| 간격 | 4px 단위. 카드 패딩 12–14px, 카드 사이 12px |
| 전환 | `transition: background-color .2s, color .2s, border-color .2s` (150–300ms) |
| 클릭 요소 | `cursor:pointer`, `:focus-visible` 2px 링 |
| 반응형 확인 폭 | 320 / 768 / 1024 / 1440px, 가로 스크롤 없음 |

## 3. 레이아웃 변경

### 3-1. 골격 (`base.html`)

```text
현재                                   변경 후
┌──────── header(고정, 끝에 붙음) ────────┐   ┌─ 12px 여백 ───────────────────────────────┐
│ 제목  검색            연결칩  주간보고 │   │ ┌ 떠 있는 내비 바(radius 14, blur) ─────┐ │
├──────┬────────────────────────────────┤   │ │ 로고 대시보드 제안 주간보고 히스토리 설정 │ │
│사이드│ main                           │   │ │        검색(Ctrl K)   연결칩  [주간보고] │ │
│ 바   │                                │   │ └───────────────────────────────────────┘ │
│220px │                                │   │ main (최대 폭 1440, 사이드바 없음)          │
└──────┴────────────────────────────────┘   └───────────────────────────────────────────┘
```

- `header`를 `position:sticky; top:12px; margin:12px 12px 0;` 둥근 바(`.topnav`)로 바꾼다. 배경 `rgba(255,255,255,.85)` + `backdrop-filter: blur(10px)`.
- `nav.side`의 메뉴를 상단 바 안 가로 메뉴(아이콘+글자)로 옮긴다. `nav_items` 루프는 그대로 쓴다.
- 검색 폼(`#search-form`)에 `Ctrl K` 표시(`<kbd>`)를 붙이고, `common.js`에 Ctrl/⌘+K로 검색 입력에 포커스하는 처리를 추가한다.
- 주간보고 초안 버튼은 CTA 색(`--cta`) + pen 아이콘.
- 900px 이하: 메뉴 글자 숨김(아이콘만), 720px 이하: 기존처럼 하단 탭바(`.tabbar`) 유지.

### 3-2. 대시보드 (`dashboard.html`, `dashboard.js`)

```text
┌ mhead: 대시보드 · 수집 상태            [연간|반기|분기|월|주] ┐
├ #mk ─────────────────────────────────────────────────────────┤
│ [KPI 완료] [KPI 준수율] [KPI 지식] [KPI 재사용] [상태 도넛]   │  ← 5열(4 KPI + 도넛)
├ grid2 ────────────────────────────────┬──────────────────────┤
│ 내 할 일   [전체|제안|진행|완료후보|임박|완료](세그먼트형)│ #detail          │
│ ☐ 업무 ⇅        모듈  요청부서  기한 ⇅  상태 │ 모듈·상태 배지     │
│ ☐ …(행 40px, 홀짝 배경)                      │ 제목             │
│ …                                            │ 키-값 표          │
│ 1–14 / 163건            ‹ 1 2 … 12 ›         │ 원본·유사 이력    │
│ [메일 새로고침] [기간 지정 수집] [+ 수동]      │ [진행 시작] …     │
└──────────────────────────────────────────────┴──────────────────┘
```

| 영역 | 변경 내용 |
|---|---|
| KPI(`#mk`) | 4개 `.kpi` 유지(테스트 조건). 각 카드 머리에 아이콘. 값은 Fira Code 22px. 추이 그래프는 높이 40px로 축소 |
| 상태 도넛 | `#mk` **밖** 형제 요소 `#status-donut`으로 추가(테스트의 `.kpi` 4개 조건 보호). `allTasks`로 계산하고 새 API는 만들지 않는다. SVG는 `svgEl`로 그린다 |
| 필터(`#filters`) | 칩 → 세그먼트 컨트롤 모양. 건수 표시는 기존 로직 유지 |
| 표(`#todo`) | 열 구성: 선택 체크박스 · 업무(제목+수신/수동) · 모듈 배지 · 요청 부서 · 기한 · 상태. 행 높이 40px, 홀짝 배경, 머리행 `position:sticky` |
| 정렬 | 업무명·기한 머리칸 클릭 시 오름→내림→해제. `aria-sort` 갱신. 정렬 상태는 메모리 변수 |
| 선택 행 | 왼쪽 3px 강조선(`box-shadow: inset 3px 0 0 var(--accent)`) + `--accent-soft` 배경 |
| 상세(`#detail`) | 키-값을 2열 표 형태로, 버튼에 아이콘. 동작 버튼 구성·조건은 기존 `selectTask()` 그대로 |
| 일괄 작업 바(선택) | 체크한 업무가 있으면 목록 하단에 떠 있는 바: "n건 선택 · 진행 시작 · 보류 · 선택 해제". 기존 `/api/tasks/{id}/status`를 건별 호출하고 실패 건수를 토스트로 알린다. **2차 범위**(아래 6장) |

### 3-3. 기타 화면

| 화면 | 변경 |
|---|---|
| 제안된 업무 | 같은 촘촘한 표 스타일. 근거 문장 열은 2줄 말줄임 + `title` 속성. 승인·무시 버튼에 아이콘 |
| 주간보고 | 3열 편집 영역 유지. 각 열 머리에 항목 수(줄 수) 표시. ①②③ 흐름을 단계 표시줄로 |
| 히스토리 | 모듈 필터를 세그먼트로. 결과 출처 배지(앱·GK·AI) 색 유지 |
| 설정·연결 | `set-row`를 카드 안 표 형태로. 연결 상태 칩에 점 표시 |
| 모달 | radius 12, 머리·본문·버튼 영역 구분선. 720px 이하 바텀시트 유지 |
| 토스트 | 오른쪽 위 카드형 + 하단 진행 막대(2.6초) |

## 4. 내 할 일 페이징 (공통 요구)

화면(모니터) 높이에 맞춰 행 수를 계산하고, 넘치면 페이지로 나눈다.

| 항목 | 명세 |
|---|---|
| 배치 | `#todo` 표 아래 `<div class="pg"><span id="todo-info"></span><nav class="pager" id="todo-pager" aria-label="내 할 일 페이지"></nav></div>` |
| 페이지 크기 | `perPage = max(5, floor((window.innerHeight − 표 시작 y − 페이저 높이 − 하단 여백) / 행 높이))`. 행 높이는 첫 행의 `getBoundingClientRect().height`로 잰다 |
| 다시 계산 | `resize`를 `requestAnimationFrame`으로 묶어 처리. 보고 있던 첫 항목이 계속 보이도록 페이지 번호를 다시 맞춘다 |
| 상태 초기화 | 필터·정렬 변경 시 1페이지. 업무 선택 시 그 업무가 있는 페이지 유지 |
| 페이저 | ‹ 1 2 … n ›. 현재 페이지 `aria-current="page"`, 버튼 최소 28px, 숫자 Fira Code |
| 안내 | `1–14 / 163건` |
| 키보드 | 목록에 포커스가 있을 때 ←→ 페이지 이동 |
| 모바일(≤720px) | 카드형 목록 1행 높이 기준으로 같은 계산 |
| 구현 위치 | `dashboard.js`의 `renderTodo()`에서 필터 → 정렬 → 페이지 자르기 순서로 처리. 페이지 계산 함수는 `paginate(list, page, perPage)`로 분리 |

**주의:** `test_dashboard_grain_switch_and_xss`는 새로 추가한 "테스트 업무"가 `#todo`에 보여야 통과한다. 데모 데이터가 많으면 새 업무가 1페이지 밖으로 밀릴 수 있다. 수동 추가 업무가 1페이지에 오도록 기본 정렬을 "최근 생성순"으로 두거나, 테스트에서 해당 업무를 찾아 페이지를 이동하도록 한다. 어느 쪽인지 구현 전에 정한다.

## 5. 파일별 작업 목록

| 파일 | 작업 |
|---|---|
| `src/app/templates/base.html` | 떠 있는 상단 내비로 재구성, 사이드바 제거, 아이콘 `<use>` 추가, 검색 `<kbd>` |
| `src/app/templates/dashboard.html` | `#status-donut`, 표 머리(체크·정렬), `#todo-info`·`#todo-pager` 추가 |
| `src/app/templates/*.html`(기타 4개·partials) | 클래스명 조정, 아이콘 추가(구조·id 유지) |
| `src/app/static/css/app.css` | 토큰 교체, 골격·표·세그먼트·페이저·토스트·반응형 규칙 재작성. 인라인 스타일 없음 |
| `src/app/static/js/common.js` | `svgEl`·`icon()` 공용화, Ctrl/⌘+K, 토스트 개선 |
| `src/app/static/js/dashboard.js` | 정렬·페이징·도넛·(2차) 일괄 작업. API 호출은 기존 그대로 |
| `src/app/static/js/charts.js` | `svgEl` 중복 제거(공용 함수 사용) |
| `src/app/static/fonts/` | Pretendard·Fira Code woff2 + 라이선스 파일 |
| `src/app/static/img/icons.svg` | Lucide 아이콘 스프라이트 + 라이선스 주석 |
| `docs/결정기록/0012_화면_디자인_개편_ui-ux-pro-max.md` | 디자인 변경 이유·범위·대안(supanova 버전) 기록 |
| `docs/변경이력.md`, `docs/요구사항추적표.md` | 12단계 화면 항목에 변경 반영 |

## 6. 진행 단계

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| 1차 | 토큰·폰트·아이콘·골격(상단 내비)·표 스타일·페이징 | 기존 테스트 전부 통과 + 페이징 테스트 통과 |
| 2차 | 열 정렬, 상태 도넛, Ctrl+K | 정렬·도넛 단위 테스트, 브라우저 테스트 |
| 3차(선택) | 체크박스 일괄 작업 | 상태 전이 규칙 위반 건은 서버가 거부하고 화면이 실패 건수를 표시 |

## 7. 검증

| 구분 | 항목 |
|---|---|
| 기존 테스트 | `tests/security/test_ui_render.py`(인라인·위험 함수·외부 리소스 금지), `tests/integration/test_ui_browser.py`(기간 전환·XSS) 전부 통과 |
| 추가 테스트 | ① 뷰포트 1440×900·1024×768·390×844에서 `#todo tr` 수가 계산된 페이지 크기 이하 ② 페이저 다음·이전·번호 클릭 ③ 필터 변경 시 1페이지 ④ 정렬 `aria-sort` 순환 ⑤ 업무 제목 `<script>` 미실행 ⑥ 폰트·아이콘 요청이 모두 같은 출처 |
| 접근성 | 대비 4.5:1, 포커스 링, 정렬 머리칸 키보드(Enter/Space), 아이콘 `aria-hidden` |
| 점검 명령 | `scripts/check.py`(포맷·린트·타입·테스트·시크릿 탐지) |
| 화면 캡처 | Playwright 캡처는 로컬에만 저장(외부 업로드 금지) |

## 8. 완료 기준

- [ ] 1장 제약을 모두 지킨다(CSP·외부 리소스·인라인·위험 함수·저장소).
- [ ] 기존 테스트 선택자와 동작이 유지된다.
- [ ] 내 할 일이 화면 높이에 맞춰 페이징되고, 창 크기 변경 시 다시 계산된다.
- [ ] 라이트·다크, 320–1440px에서 가로 스크롤 없이 보인다.
- [ ] 폰트·아이콘 라이선스 파일이 함께 있다.
- [ ] 결정기록·변경이력·요구사항추적표를 갱신했다.

## 참고

- ui-ux-pro-max 문서(카탈로그 구성, 출력 형식, 체크리스트): https://docs.claudekit.cc/docs/engineer/skills/ui-ux-pro-max
- Lucide 아이콘(ISC): https://lucide.dev
- Pretendard(OFL 1.1): https://github.com/orioncactus/pretendard
- Fira Code(OFL 1.1): https://github.com/tonsky/FiraCode
