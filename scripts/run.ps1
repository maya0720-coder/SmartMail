# 업무 처리 현황 - 로컬 실행 스크립트(Windows PowerShell)
# 사용법(저장소 루트에서):  powershell -ExecutionPolicy Bypass -File scripts\run.ps1
#   -Install : 가상환경 생성과 운영 의존성 설치(해시 검증)까지 수행
#   -Demo    : 이번 실행만 데모 모드(가상 데이터, 외부 연동 없음)로 실행
#   -Open    : 서버가 뜨면 기본 브라우저로 화면을 연다(실행 파일이 사용)
param(
    [switch]$Install,
    [switch]$Demo,
    [switch]$Open
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

# 이전에 띄운 앱이 같은 포트에 남아 있으면(창을 닫아도 프로세스가 남는 경우) 사용자에게 물어보고 종료한다.
# 이 앱(app.main) 프로세스일 때만 종료를 제안한다. 다른 프로그램이면 안내만 하고 멈춘다.
$Port = & $Python -c "from config.settings import load_settings; print(load_settings().app_port)"
if (-not $?) { throw "설정을 읽지 못했습니다(.env 확인)." }
$Listener = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort ([int]$Port) -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($Listener) {
    $Owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($Listener.OwningProcess)"
    if ($Owner -and $Owner.CommandLine -match "app\.main") {
        Write-Host ""
        Write-Host "이전에 실행한 앱이 아직 실행 중입니다 (PID $($Owner.ProcessId), 시작 $($Owner.CreationDate))."
        $Answer = Read-Host "종료하고 새 버전으로 다시 시작할까요? (Y/N)"
        if ($Answer -match "^[Yy]") {
            Stop-Process -Id $Owner.ProcessId
            $Deadline = (Get-Date).AddSeconds(15)
            while ((Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort ([int]$Port) -State Listen -ErrorAction SilentlyContinue) -and (Get-Date) -lt $Deadline) {
                Start-Sleep -Milliseconds 300
            }
            Write-Host "이전 앱을 종료했습니다. 새로 시작합니다."
        } else {
            Write-Host "기존 앱을 그대로 씁니다: http://127.0.0.1:$Port/"
            if ($Open) { Start-Process "http://127.0.0.1:$Port/" }
            return
        }
    } else {
        throw "포트 $Port 를 다른 프로그램(PID $($Listener.OwningProcess))이 쓰고 있습니다. .env의 SMARTMAIL_APP_PORT를 바꾸거나 그 프로그램을 종료해 주세요."
    }
}

try {
    if ($Open) { & $Python -m app.main --open } else { & $Python -m app.main }
}
finally {
    if ($Demo) { Remove-Item Env:SMARTMAIL_DEMO_MODE -ErrorAction SilentlyContinue }
}
