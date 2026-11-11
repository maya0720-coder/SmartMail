# 06. AI-Ground g-llm 연동

메인 문서: `dev/00_MAIN_개발진행.md` · 다음 단계: `dev/07_업무추출_To-do.md`

## 목표

업무 분류(07단계)와 해결 방안 제안(09단계)에 쓸 g-llm 어댑터를 만든다. 외부로 나가는 내용은 마스킹된 요약뿐이다.

## 관련 요구사항

AI-01 ~ AI-08

## 작업

1. `src/integrations/ai/base.py`: `KnowledgeAI` 인터페이스 — `classify_task(MaskedText)`, `suggest_solution(MaskedText, similar_cases)`. 인자는 `MaskedText`만(AI-02, AI-03).
2. `src/integrations/ai/ground_gllm.py`: AI-Ground(https://ai-ground.golfzon.company/) g-llm 호출. **API 엔드포인트·인증 방식·요청 형식은 `확인 필요`** — 확인 전에는 설정 placeholder와 미구현 예외만 두고 추측 구현하지 않는다. 로그인 필요 시 04단계 사용(AI-05).
3. `src/integrations/ai/fake.py`: 테스트·데모용 Fake(고정 응답).
4. 응답 처리(AI-04): '근거 링크'와 'AI 생성·검증 필요' 표시 필드 포함. 응답을 HTML로 보여줄 때 출력 인코딩.
5. 호출 이력(AI-06): 시각·요청 요약(마스킹)·성공/실패만 로컬 기록.
6. 어댑터 선택은 설정값(AI-08). 타임아웃·재시도·실패 시 사용자 안내.

## 검증(필수 테스트)

- 마스킹 안 된 문자열은 호출 불가(타입 검사)
- 전송 직전 페이로드에 메일 원문 전체·자격증명이 없는지 검사
- 타임아웃·오류 응답 시 안전한 실패
- 응답 속 스크립트 문자열이 화면에서 실행되지 않음(XSS)

## 완료 기준

- [ ] 인터페이스·Fake 완료, 실제 g-llm 어댑터는 규격 확인 후 완료(그 전까지 상태 `보류(확인 필요)` 가능)
