# 업무 처리 현황 - 로컬 실행 스크립트(Windows PowerShell)
# 사용법(저장소 루트에서):  powershell -ExecutionPolicy Bypass -File scripts\run.ps1
#   -Install : 가상환경 생성과 운영 의존성 설치(해시 검증)까지 수행
#   -Demo    : 이번 실행만 데모 모드(가상 데이터, 외부 연동 없음)로 실행
param(
    [switch]$Install,
    [switch]$Demo
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"

if ($Install -or -not (Test-Path $Python)) {
    Write-Host "가상환경을 만듭니다(Python 3.11 필요)..."
    py -3.11 -m venv .venv
    if (-not $?) { throw "Python 3.11 가상환경을 만들지 못했습니다. Python 3.11을 설치해 주세요." }
    & $Python -m pip install --upgrade pip
    # 해시가 고정된 잠금 파일로만 설치한다(공급망 무결성).
    & $Python -m pip install --require-hashes --no-deps -r requirements.lock
    if (-not $?) { throw "의존성 설치에 실패했습니다." }
    # G-Knowledge 브라우저 자동화용 Chromium(Playwright 공식 배포본)
    & $Python -m playwright install chromium chromium-headless-shell
}

if (-not (Test-Path (Join-Path $Root ".env"))) {
    throw ".env 파일이 없습니다. .env.example을 복사해 값을 채워 주세요(docs/로컬실행가이드.md 참고)."
}

$env:PYTHONPATH = Join-Path $Root "src"
$env:PYTHONIOENCODING = "utf-8"
if ($Demo) { $env:SMARTMAIL_DEMO_MODE = "true" }
try {
    & $Python -m app.main
}
finally {
    if ($Demo) { Remove-Item Env:SMARTMAIL_DEMO_MODE -ErrorAction SilentlyContinue }
}
