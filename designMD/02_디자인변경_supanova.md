# 디자인 변경 명세 — Supanova 버전

> 대상: SmartMail(업무 처리 관리 현황, 로컬 테스트 버전) 화면 레이어
> 작성일: 2026-11-12 · 상태: 제안(사용자 확인 전) · 비교 시안: `D:\00AI_Workspace\ui-patterns\skill-samples\06_supanova.html`
> 규칙 출처: 빌더 조쉬 영상(https://youtu.be/2sNQ0Nvngdc)에서 추천한 Supanova 디자인 스킬의 공개 페이지. 상세 정리는 `ui-patterns\skill-samples\supanova_디자인툴_가이드.md`

## 0. 개요

| 항목 | 내용 |
|---|---|
| 목적 | 매일 여는 업무 화면을 '고급 에이전시 수준'의 인상으로 바꾼다. 여백·그림자·모션을 다듬고, 이번 주 핵심(주간보고·기한 지남)을 첫 화면 상단에 크게 보여준다. |
| 디자인 기준 | Supanova 스킬(`supanova-premium-aesthetic`, `supanova-design-engine`) 공개 규칙 |
| 변경 범위 | `src/app/templates/`, `src/app/static/css/app.css`, `src/app/static/js/`(화면 스크립트만), 신규 정적 리소스(폰트·아이콘) |
| 변경하지 않는 것 | API·도메인·저장소·보안 미들웨어(`web_security.py`)·요구사항 동작. 화면 표현만 바꾼다. |
| 핵심 변화 | 떠 있는 알약형 내비, 비대칭 벤토(주간보고 히어로 + KPI + '먼저 챙길 일'), Pretendard + Solar 듀오톤 아이콘, 겹친 부드러운 그림자, 블러→선명 등장 모션, **내 할 일 화면 높이 기준 페이징** |

### 0-1. 스킬 원본과 다르게 적용하는 부분

| 스킬 원본 | 이 프로젝트 적용 | 이유 |
|---|---|---|
| 랜딩 페이지·전환율 목적, `VISUAL_DENSITY: 3` | 업무 대시보드, 밀도 **5** | 업무 목록은 한 화면에 충분히 보여야 함 |
| Tailwind CDN, 단일 HTML | 기존 FastAPI 템플릿 + `app.css` 유지 | CSP·외부 CDN 금지(1장) |
| Iconify 웹 컴포넌트 | Solar 아이콘을 로컬 SVG 스프라이트로 | 외부 API 호출 금지, `innerHTML` 금지 |
| Gemini 배경 영상, Netlify Drop 배포 | 사용 안 함. 배경은 CSS 그라데이션(오로라) | 외부 전송·외부 공개 금지(개발지침 4장) |

이 차이는 `docs/결정기록/0012_화면_디자인_개편_supanova.md`에 남기고 사용자 확인을 받는다(개발지침 0-1 규칙 10).

## 1. 반드시 지킬 제약 (현재 코드에서 확인)

| 제약 | 근거 | 이 버전에서의 처리 |
|---|---|---|
| CSP `default-src 'self'`, `style-src 'self'`, `font-src 'self'`, 외부 CDN 금지 | `web_security.py` CSP, `test_no_external_resources` | Pretendard·Solar 아이콘 모두 `src/app/static/`에 로컬 번들 |
| 템플릿 인라인 `<script>`·`style=`·`on*=` 금지 | `test_templates_have_no_inline_script_style_or_handlers` | 등장 지연(`--d`) 같은 값도 CSS 클래스(`.d1`~`.d4`)로 처리. 인라인 `style` 속성 사용 금지 |
| `innerHTML` 등 위험 함수, 브라우저 저장소 금지 | `test_scripts_do_not_use_unsafe_sinks_or_storage` | 아이콘은 `createElementNS`+`<use>`, 상태는 메모리 변수 |
| 기존 화면 테스트 선택자 유지 | `test_ui_browser.py` | `#todo tr`, `#seg button[data-g]`, `#mk .kpi`(4개), `#filters`, `#detail` 유지 |
| 출력 인코딩 | `common.js`의 `el()` | 모든 텍스트는 `el()`로만 출력 |
| 스크롤 이벤트 금지(스킬 규칙) | Supanova premium-aesthetic | 등장 모션은 `IntersectionObserver`만 사용 |

## 2. 디자인 시스템

### 2-1. 색 토큰 (`app.css` `:root` 교체)

| 토큰 | 라이트 | 다크 | 용도 |
|---|---|---|---|
| `--bg` | `#f5f4f0`(따뜻한 미색) | `#0d0f12` | 페이지 배경 |
| `--card` | `#ffffff` | `#16191e` | 카드 |
| `--soft` | `#efede7` | `#1f232a` | 보조 배경·칩·키-값 칸 |
| `--ring` | `rgba(13,15,18,.06)` | `rgba(255,255,255,.06)` | 카드 안쪽 윤곽(회색 1px 실선 대체) |
| `--fg` / `--sub` / `--muted` | `#0d0f12` / `#4a4f57` / `#9298a1` | `#eef0f3` / `#a7adb6` / `#6b7280` | 글자 |
| `--accent` / `--accent-2` | `#4f46e5` / `#7c3aed` | `#818cf8` / `#a78bfa` | 강조·브랜드 그라데이션 |
| `--accent-soft` | `#eef0ff` | `#1e1b4b` | 선택 행·진행 중 배지 |
| `--warn` / `--warn-soft` | `#e8590c` / `#fff1e8` | `#fb923c` / `#3b2414` | 기한 지남·임박 |
| `--success` / `--success-soft` | `#0f9d58` / `#e7f6ee` | `#34d399` / `#0f2e22` | 완료 후보·완료 |
| `--ai` / `--gk` | 기존 값 유지 | 기존 값 유지 | AI·G-Knowledge 구분색 |

### 2-2. 그림자·모양

| 항목 | 값 | 규칙 |
|---|---|---|
| 카드 그림자 | `0 1px 2px rgba(13,15,18,.04), 0 18px 40px -18px rgba(13,15,18,.16)` + `inset 0 0 0 1px var(--ring)` | `shadow-md`·`rgba(0,0,0,.3)`·`1px solid gray` 금지 |
| 모서리 | 큰 카드 28px, 목록 행 18px, 버튼·칩 999px(알약형), 키-값 칸 16px | |
| 구분선 | 표 행 사이 선 없음 → 간격과 hover 배경으로 구분 | 회색 실선 금지 |

### 2-3. 타이포그래피

| 역할 | 값 |
|---|---|
| 글꼴 | Pretendard Variable(로컬 woff2, `src/app/static/fonts/`, OFL 1.1 라이선스 동봉) |
| 금지 글꼴 | Inter, Noto Sans KR, Roboto, Arial, Open Sans, Helvetica, Malgun Gothic (스킬 규칙) |
| 대체 스택 | `"Pretendard Variable","Pretendard",-apple-system,"Apple SD Gothic Neo",sans-serif` — 로컬 번들이 있으므로 맑은 고딕으로 떨어지지 않음 |
| 크기 | 히어로 제목 34px/800/자간 -0.045em, h2 22px, 섹션 제목 19px, 본문 14px, 행 제목 14.5px/600 |
| 숫자 | `font-variant-numeric: tabular-nums` |
| 문구 | "혁신적인·원활한·차세대·한 차원 높은·게임 체인저" 금지. 사실과 숫자로 쓴다(예: "메일 163건에서 이번 주 할 일을 골라냈어요") |

### 2-4. 아이콘

- **Solar 아이콘 세트(480 Design, CC BY 4.0)만** 사용. 굵은 Lucide·FontAwesome·Material Icons 금지(스킬 규칙).
- 스타일: `bold-duotone` 기본, 검색·화살표만 `linear`.
- 구현: `npm` 패키지 `@iconify-json/solar`에서 필요한 아이콘만 뽑아 `src/app/static/img/solar.svg` 스프라이트(`<symbol>`)로 만든다. 실행 중 외부 호출 없음.
- 출처 표기: 설정·연결 화면 하단에 "Icons: Solar by 480 Design (CC BY 4.0)" 문구, `src/app/static/img/LICENSE-solar.txt`.
- 사용 아이콘(예): widget-5, inbox-line, document-text, history, settings, magnifer, pen-new-square, arrow-right, calendar-mark, danger-triangle, play-circle, check-circle, layers-minimalistic, letter, book-bookmark, target, stars-minimalistic, clock-circle.

### 2-5. 모션

| 항목 | 값 | 규칙 |
|---|---|---|
| 기본 곡선 | `--e: cubic-bezier(.16,1,.3,1)` | `linear`·`ease-in-out` 금지 |
| 시간 | 상태 변화 .35s, 등장 .9s, 버튼 hover .45–.5s | 즉시 바뀌는 상태 변화 금지 |
| 등장 | 카드가 `opacity 0 → 1`, `translateY(18px) → 0`, `blur(4px) → 0`. 순서 지연은 `.d1`(80ms)~`.d4`(320ms) 클래스 | `IntersectionObserver`로 `.in` 클래스 부여 |
| 페이지 넘김 | 새 행이 35ms 간격으로 아래에서 올라옴(`@keyframes`) | |
| 히어로 배경 | 그라데이션 3개가 18초 주기로 천천히 흐름. 포인터를 따라 약간 이동(`pointermove` + `requestAnimationFrame`) | 스크롤 이벤트 미사용 |
| 동작 줄이기 | `prefers-reduced-motion: reduce`이면 모든 등장·흐름 정지 | |

## 3. 레이아웃 변경

### 3-1. 골격 (`base.html`)

```text
현재                                     변경 후
┌──────── header(끝에 붙은 고정) ───────┐   ┌──────── 16px 여백 ─────────────────────────┐
│ 제목  검색          연결칩  주간보고 │   │      ( 로고 · 대시보드 · 제안 · 주간보고 ·    │
├──────┬───────────────────────────────┤   │        히스토리 · 설정 · 검색 · 연결점 )     │ ← 가운데 떠 있는 알약
│사이드│ main                          │   │                                             │
│ 바   │                               │   │ main (최대 폭 1440, 좌우 28px)               │
└──────┴───────────────────────────────┘   └─────────────────────────────────────────────┘
```

- `header` → `.pillnav`: `position:sticky; top:16px; margin:16px auto 0; width:fit-content;` 반투명 흰 배경 + `backdrop-filter: blur(16px) saturate(1.4)`, 알약형. 화면 끝에 붙지 않는다(스킬 규칙).
- 사이드바(`nav.side`) 제거, 메뉴는 알약 안으로. 현재 메뉴는 검정 알약(`--fg` 배경, 흰 글자).
- '주간보고 초안' 버튼은 상단에서 빼고 대시보드 히어로 카드로 옮긴다(다른 화면에서는 알약 오른쪽 끝에 작게 유지).
- 연결 상태(`#conn`)는 칩 대신 작은 점 4개(위키·G-Knowledge·AI-Ground·그룹웨어) + 마우스를 올리면 이름.
- 900px 이하: 메뉴 글자 숨김(아이콘만), 720px 이하: 하단 탭바 유지(알약형 스타일로).

### 3-2. 대시보드 — 비대칭 벤토 (`dashboard.html`, `dashboard.js`)

```text
┌ mhead: 대시보드 · 수집 상태                        [연간 반기 분기 월 주] ┐
├──────────────────────────────────────────────┬─────────────────────────┤
│ HERO (검정 바탕 + 오로라 그라데이션)            │ 먼저 챙길 일 (#soon)      │
│ 11.05 목 – 11.11 수 · 주간보고 46주차          │ ◇ 시스템 카피 일정  10/16 지남 │
│ 메일 163건에서 이번 주 할 일을 골라냈어요       │ ◇ 예산과목 개선     11/10 지남 │
│                         [주간보고 초안 만들기 →]│ ◇ 인터페이스 정의서 11/10 지남 │
├───────────┬───────────┬───────────┬──────────┴─────────────────────────┤
│ KPI 완료  │ KPI 준수율 │ KPI 지식  │ KPI 재사용   ← #mk .kpi 4개(크기 다르게 가능) │
├───────────┴───────────┴───────────┴───────────────┬────────────────────┤
│ 내 할 일              [전체·제안·진행·완료후보·임박·완료] │ #detail             │
│ ▢ 아이콘  제목 / 모듈·수신시각   부서   기한   상태 │ 상태 배지            │
│ (행 62px, 선 없음, hover 배경)                     │ 제목 22px            │
│ 1–6 / 163건                       ( ‹ 1 2 … 28 › ) │ 근거 인용(그라데이션) │
│ [메일 새로고침] [기간 지정 수집] [+ 수동 업무]       │ 키-값 2×2 / 버튼     │
└──────────────────────────────────────────────────┴────────────────────┘
```

| 영역 | 변경 내용 |
|---|---|
| 히어로(`#hero`, 신규) | 검정 카드(28px) + CSS 오로라. 이번 보고 주간(목~수)과 실제 숫자 한 문장, '주간보고 초안 만들기' 흰 알약 버튼(오른쪽 원형 화살표). 숫자는 `/api/tasks`·`/api/metrics` 결과만 쓰고 지어내지 않는다 |
| 먼저 챙길 일(`#soon`, 신규) | `allTasks`에서 기한 지남 → 기한 가까운 순으로 3건. 누르면 해당 업무로 이동(페이지 계산 후 선택). 새 API 없음 |
| KPI(`#mk`) | `.kpi` 4개 유지. 벤토 아래 줄에 배치, 카드 28px·부드러운 그림자, 추이 그래프 유지(높이 48px). '참고' 배지 유지 |
| 필터(`#filters`) | 회색 바탕 알약 그룹 안의 흰 알약 선택 표시 |
| 표(`#todo`) | `table` 구조는 유지하되 행을 카드처럼 보이게(`td` 사이 선 제거, 행 radius 18px, 높이 62px). 첫 칸에 모듈별 Solar 아이콘 타일(40px, radius 14). 기한 지남은 경고 아이콘 + 주황 |
| 상세(`#detail`) | 상태 배지 → 제목(22px) → 근거 인용 박스(연보라 그라데이션) → 키-값 2×2 타일 → 원본·유사 이력 버튼 → 하단 고정 행동 버튼(검정 알약 '진행 시작'/'완료 확정'). 버튼 구성·조건은 기존 `selectTask()` 그대로 |
| 보관 D-day·알림(`#alerts`) | 주황 연한 알약 배너 |

### 3-3. 기타 화면

| 화면 | 변경 |
|---|---|
| 제안된 업무 | 행 카드형(선 없음). 근거 문장은 연보라 인용 박스. 승인=검정 알약, 무시=회색 알약 |
| 주간보고 | 3열 편집 영역을 큰 카드 3개로, 열 머리에 Solar 아이콘. ①②③ 흐름을 알약 단계 표시줄로. 게시 버튼은 검정 알약 |
| 히스토리 | 검색창 큰 알약형, 결과 출처 배지(앱·GK·AI) 알약형 |
| 설정·연결 | 행마다 카드(28px), 위험 동작(전체 삭제)은 주황 테두리 없이 경고 아이콘 + 확인 모달. 하단 아이콘 출처 문구 |
| 모달 | radius 28px, 배경 흐림(`backdrop-filter`), 등장 시 scale .98→1. 720px 이하 바텀시트 유지 |
| 토스트 | 가운데 아래 반투명 검정 알약 + 블러, 등장 시 블러→선명 |

## 4. 내 할 일 페이징 (공통 요구)

| 항목 | 명세 |
|---|---|
| 배치 | `#todo` 아래 `<div class="pg"><span id="todo-info"></span><nav class="pager" id="todo-pager" aria-label="내 할 일 페이지"></nav></div>` |
| 페이지 크기 | `perPage = max(4, floor((window.innerHeight − 표 시작 y − 페이저 높이 − 하단 여백) / 행 높이))`. 행 높이(62px)는 첫 행을 재서 쓴다 |
| 다시 계산 | `resize`를 `requestAnimationFrame`으로 묶어 처리. 보던 첫 항목이 계속 보이도록 페이지 번호 재계산 |
| 상태 초기화 | 필터 변경 시 1페이지. '먼저 챙길 일'에서 이동 시 해당 업무가 있는 페이지로 |
| 페이저 | 회색 알약 그룹 안 ‹ 1 2 … n ›, 현재 페이지 검정 원. `aria-current="page"` |
| 키보드 | 목록 포커스 시 ←→ 페이지, ↑↓ 행 선택 |
| 모바일(≤720px) | 카드형 행 높이 기준 같은 계산, 히어로·먼저 챙길 일은 세로로 쌓고 '먼저 챙길 일'은 숨김 |
| 구현 위치 | `dashboard.js`의 `renderTodo()`에서 필터 → 페이지 자르기. `paginate(list, page, perPage)` 함수 분리 |

**주의:** `test_dashboard_grain_switch_and_xss`는 새로 추가한 "테스트 업무"가 `#todo`에 보여야 통과한다. 데모 데이터가 많으면 새 업무가 1페이지 밖으로 밀릴 수 있다. 기본 정렬을 "최근 생성순"으로 두거나 테스트가 해당 업무 페이지로 이동하도록 고친다. 구현 전에 정한다.

## 5. 파일별 작업 목록

| 파일 | 작업 |
|---|---|
| `src/app/templates/base.html` | 알약형 내비로 재구성, 사이드바 제거, Solar 아이콘 `<use>`, 연결 점 |
| `src/app/templates/dashboard.html` | `#hero`, `#soon` 추가, 벤토 그리드 래퍼, `#todo-info`·`#todo-pager` 추가, 등장 클래스(`.rv .d1`~`.d4`) |
| `src/app/templates/*.html`(기타 4개·partials) | 카드·알약 클래스 적용, 아이콘 추가(구조·id 유지) |
| `src/app/static/css/app.css` | 토큰·그림자·모양·모션 재작성, 벤토·히어로 오로라(`@keyframes`)·페이저·토스트·모달·반응형 |
| `src/app/static/js/common.js` | `svgEl`·`icon(name)` 공용화, 등장 모션(`IntersectionObserver`), 토스트 개선 |
| `src/app/static/js/dashboard.js` | 히어로 문장·먼저 챙길 일 렌더링, 페이징, 히어로 포인터 이동, CTA 진행 표시(작성 중 → 완료) |
| `src/app/static/js/charts.js` | `svgEl` 중복 제거, 선 색을 `--accent`로 |
| `src/app/static/fonts/` | Pretendard Variable woff2 + `LICENSE-pretendard.txt` |
| `src/app/static/img/solar.svg` | Solar 아이콘 스프라이트 + `LICENSE-solar.txt` |
| `docs/결정기록/0012_화면_디자인_개편_supanova.md` | 0-1장 차이·범위·대안(ui-ux-pro-max 버전) 기록 |
| `docs/변경이력.md`, `docs/요구사항추적표.md` | 12단계 화면 항목에 변경 반영 |

## 6. 진행 단계

| 단계 | 내용 | 완료 기준 |
|---|---|---|
| 1차 | 토큰·폰트·아이콘·알약 내비·카드 스타일·페이징 | 기존 테스트 전부 통과 + 페이징 테스트 통과 |
| 2차 | 벤토(히어로·먼저 챙길 일), 상세 패널 재배치 | 히어로 숫자가 API 값과 일치, 먼저 챙길 일 정렬 테스트 |
| 3차 | 모션(등장·오로라·페이지 넘김), 모달·토스트 | 동작 줄이기 설정 시 모션 정지 확인, 콘솔 오류 없음 |

## 7. 검증

| 구분 | 항목 |
|---|---|
| 기존 테스트 | `tests/security/test_ui_render.py`, `tests/integration/test_ui_browser.py` 전부 통과 |
| 추가 테스트 | ① 뷰포트 1440×900·1024×768·390×844에서 `#todo tr` 수 ≤ 계산된 페이지 크기 ② 페이저 이동 ③ 필터 변경 시 1페이지 ④ '먼저 챙길 일' 클릭 시 해당 업무 선택·페이지 이동 ⑤ 히어로 숫자 = API 값 ⑥ 업무 제목 `<script>` 미실행 ⑦ 모든 폰트·아이콘 요청이 같은 출처 ⑧ `prefers-reduced-motion`에서 애니메이션 없음 |
| 스킬 규칙 점검 | `app.css`에 `linear`·`ease-in-out`·`1px solid` 회색·`rgba(0,0,0,.3)` 없음, JS에 `addEventListener("scroll"` 없음, 금지 글꼴 이름 없음, 화면 문구에 금지 표현 없음 → 간단한 정규식 테스트로 자동화 |
| 접근성 | 대비 4.5:1(오로라 위 흰 글자 포함), 포커스 링, 아이콘 `aria-hidden` |
| 점검 명령 | `scripts/check.py`(포맷·린트·타입·테스트·시크릿 탐지) |
| 화면 캡처 | Playwright 캡처는 로컬에만 저장(외부 업로드 금지) |

## 8. 완료 기준

- [ ] 1장 제약을 모두 지킨다(CSP·외부 리소스·인라인·위험 함수·저장소·스크롤 이벤트).
- [ ] 기존 테스트 선택자와 동작이 유지된다.
- [ ] 내 할 일이 화면 높이에 맞춰 페이징되고, 창 크기 변경 시 다시 계산된다.
- [ ] 히어로·먼저 챙길 일의 숫자는 실제 API 값만 쓴다.
- [ ] 라이트·다크, 320–1440px에서 가로 스크롤 없이 보인다.
- [ ] Pretendard(OFL)·Solar(CC BY 4.0) 라이선스 파일과 출처 표기가 있다.
- [ ] 결정기록·변경이력·요구사항추적표를 갱신했다.

## 참고

- 영상: https://youtu.be/2sNQ0Nvngdc (빌더 조쉬 Builder Josh)
- Supanova premium-aesthetic: https://skills.sh/uxjoseph/supanova-design-skill/supanova-premium-aesthetic
- Supanova design-engine: https://skills.sh/uxjoseph/supanova-design-skill/supanova-design-engine
- Solar 아이콘(480 Design, CC BY 4.0): https://www.figma.com/community/file/1166831539721848736
- Pretendard(OFL 1.1): https://github.com/orioncactus/pretendard
