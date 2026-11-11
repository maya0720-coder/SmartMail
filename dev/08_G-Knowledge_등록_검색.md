# 08. G-Knowledge 등록 · 검색 (브라우저 자동화)

메인 문서: `dev/00_MAIN_개발진행.md` · 다음 단계: `dev/09_이력조회_히스토리.md`

## 목표

업무 처리 내용을 버튼 하나로 G-Knowledge(https://gk.golfzon.company/knowledge/new)에 등록하고, 이력 조회 2단계에서 G-Knowledge를 검색한다.

## 관련 요구사항

GK-01 ~ GK-07, RET-03(R&R 항목 포함)

## 작업

1. `src/integrations/gknowledge/base.py`: `KnowledgeStore` 인터페이스 — `register(MaskedDraft) -> 등록 결과(링크)`, `search(MaskedText) -> 결과 목록`.
2. 등록 초안(GK-02, RET-03): 제목, 본문(문제·원인·조치), 태그, R&R(업무명·담당자·요청 부서·처리일·모듈). 생성 시 마스킹(GK-07).
3. `src/integrations/gknowledge/browser.py`(GK-03·04): Playwright 비저장 컨텍스트로 등록 화면에 입력·제출. **화면 필드 선택자와 검색 화면 구조는 `확인 필요`** — 필드 매핑을 설정 파일(`config/gknowledge_fields.yaml` 등)로 분리하고, 확인 전에는 Fake로만 동작.
4. 로그인(GK-05): 04단계 로그인 창. 세션 만료 시 다시 요청.
5. 결과 연결(GK-06): 등록 링크·시각을 업무에 저장, 중복 등록 방지(재등록은 명시적 버튼).
6. 등록·검색 이벤트 기록(지표: 지식 등록률, 이력 재사용률).

## 검증(필수 테스트)

- Fake 저장소로 등록·중복 방지·실패 처리
- 등록 초안에 민감정보가 마스킹됨
- 브라우저 컨텍스트 종료 후 프로필 폴더가 남지 않음
- 실제 사이트 자동화는 수동 점검 항목(사용자 승인 후, 테스트 계정·테스트 글로 1회)

## 완료 기준

- [ ] 인터페이스·Fake·미리보기 흐름 완료, 실제 자동화는 필드 확인 후 완료
