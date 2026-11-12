@echo off
rem 업무 처리 현황 - 실행 파일(더블클릭). 로컬 서버를 켜고 브라우저로 화면을 연다.
rem 종료: 이 창에서 Ctrl+C 또는 창 닫기. 종료 시 모든 사이트 세션과 브라우저 임시 폴더를 지운다.
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" -Open
echo.
echo 서버가 종료되었습니다. 창을 닫으려면 아무 키나 누르세요.
pause >nul
