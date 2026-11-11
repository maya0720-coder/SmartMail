# 전체 점검 실행(포맷·린트·타입·테스트·시크릿 탐지). 인자는 check.py로 그대로 전달한다.
# 예: .\scripts\check.ps1 --audit
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "가상환경이 없습니다. 'py -3.11 -m venv .venv' 후 '.venv\Scripts\pip-sync requirements-dev.lock'을 실행하세요."
    exit 1
}
& $python (Join-Path $PSScriptRoot "check.py") @args
exit $LASTEXITCODE
