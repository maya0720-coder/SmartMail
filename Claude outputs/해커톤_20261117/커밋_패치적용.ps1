# SmartMail 커밋·푸시·해커톤 v2 패치 적용 스크립트 (Claude 작성)
# 위치: <저장소>\Claude outputs\해커톤_20261117\  — 저장소 루트는 두 단계 위
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = (Resolve-Path (Join-Path $here '..\..')).Path
$patch = Join-Path $here 'SmartMail_해커톤_v2.patch'
$log = Join-Path $here ('실행기록_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.txt')
Start-Transcript -Path $log | Out-Null
Set-Location $repo
$exclude = ':(exclude)Claude outputs'

function Step($t) { Write-Host ''; Write-Host "==== $t ====" -ForegroundColor Cyan }
function Ask($q) { (Read-Host "$q (Y/N)") -match '^[Yy]' }
function Git { & git @args; if ($LASTEXITCODE -ne 0) { throw "git $($args -join ' ') 실패 (코드 $LASTEXITCODE)" } }
function Trailer {
  "`n`nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`nClaude-Session: https://claude.ai/code/session_01A8CiS3oH9HeHC2tmrwN38d"
}
function CommitAll($msg) {
  Git add -A -- . $exclude
  $staged = & git diff --cached --name-only
  if (-not $staged) { Write-Host '커밋할 변경 없음 — 건너뜀'; return $false }
  $bad = $staged | Where-Object { $_ -match '(^|/)\.env($|\.)' -and $_ -notmatch '\.env\.example$' -or $_ -match '\.(db|sqlite3?)$' }
  if ($bad) { Git reset -q; throw "비밀정보·DB 파일이 포함됨: $($bad -join ', ') — 중단" }
  Write-Host "커밋 대상 $(@($staged).Count)개:"; $staged | ForEach-Object { "  $_" }
  if (-not (Ask '이대로 커밋할까요?')) { Git reset -q; throw '사용자가 커밋 취소' }
  $f = New-TemporaryFile
  [IO.File]::WriteAllText($f.FullName, $msg + (Trailer), (New-Object Text.UTF8Encoding $false))
  Git commit -F $f.FullName; Remove-Item $f.FullName; return $true
}
function PushBranch {
  $b = (& git rev-parse --abbrev-ref HEAD).Trim()
  if (Ask "origin/$b 로 푸시할까요?") { Git push -u origin $b } else { Write-Host '푸시 생략' }
}

try {
  Step '0. 저장소 확인'
  Write-Host "저장소: $repo"
  Git rev-parse --is-inside-work-tree | Out-Null
  Write-Host "브랜치: $((& git rev-parse --abbrev-ref HEAD).Trim())"
  if (-not (Test-Path $patch)) { throw "패치 파일 없음: $patch" }
  Git status --short -- . $exclude

  Step '1. 현재 버전 커밋'
  CommitAll 'chore: 해커톤 작업 전 현재 개발 버전 저장' | Out-Null

  Step '2. 현재 버전 푸시'
  PushBranch

  Step '3. 해커톤 v2 패치 적용 (가상데이터 116건·가상/실제 전환·설치본 스크립트)'
  & git apply --check $patch
  if ($LASTEXITCODE -eq 0) { Git apply --whitespace=nowarn $patch }
  else {
    Write-Host '그대로 적용 불가 — 3-way 병합으로 재시도' -ForegroundColor Yellow
    Git apply --3way --whitespace=nowarn $patch
  }
  Write-Host '패치 적용 완료'

  Step '4. 점검 (scripts\check.py)'
  $py = Join-Path $repo '.venv\Scripts\python.exe'
  if (Test-Path $py) {
    & $py scripts\check.py
    $ok = ($LASTEXITCODE -eq 0)
    Write-Host ("점검 결과: " + ($(if ($ok) {'통과'} else {"실패 (코드 $LASTEXITCODE)"})))
  } else { $ok = $false; Write-Host '.venv 없음 — 점검 생략' -ForegroundColor Yellow }
  if (-not $ok -and -not (Ask '점검이 통과하지 않았습니다. 그래도 커밋할까요?')) { throw '점검 실패로 커밋 보류 (패치는 작업 트리에 적용된 상태)' }

  Step '5. 해커톤 변경분 커밋'
  CommitAll "feat(시연): 영업팀 가상데이터 116건·가상/실제 전환·심사 제출용 설치본 스크립트`n`n- 요구사항: 시연(가상/실제 전환) — docs/요구사항추적표.md`n- 결정기록: docs/결정기록/0042_가상실제_전환_영업팀_가상데이터.md" | Out-Null

  Step '6. 푸시'
  PushBranch

  Step '완료'; Git log --oneline -3
} catch {
  Write-Host "중단: $($_.Exception.Message)" -ForegroundColor Red
} finally {
  Stop-Transcript | Out-Null
  Write-Host "실행 기록: $log"
  Read-Host '엔터를 누르면 닫힙니다' | Out-Null
}
