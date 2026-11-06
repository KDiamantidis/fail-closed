<#
.SYNOPSIS
  Plan B: run the public demo on this Windows laptop (no VM). Local only: this script never opens
  anything to the internet. The Cloudflare tunnel is a separate, manual step (docs/deploy.md).

.EXAMPLE
  .\deploy\planb.ps1 start          # check RAM + prerequisites, start the API on 127.0.0.1:8000
  .\deploy\planb.ps1 status         # what runs, sandbox count, free RAM, today's AI spend
  .\deploy\planb.ps1 stop           # stop the API and remove every Plan B sandbox container
  .\deploy\planb.ps1 start -Rehearsal   # local test without ADMIN_TOKEN (admin actions disabled)

  Same settings as the VM: PUBLIC_DEMO=true, the same per-IP limits, a $1/day AI budget, the AI on
  Vultr Serverless Inference (LLM_MODE=live: no key = AI off, never the mock), warm sandbox pool.
  For the tunnel: updates by polling (LIVE_UPDATES=poll), the visitor IP from CF-Connecting-IP
  (TRUSTED_PROXY=cloudflared), the API on 127.0.0.1 only.
  Secrets are read by the API from the local .env; this script only checks that they are set and
  never prints them.
#>
param(
  [Parameter(Position = 0, Mandatory = $true)][ValidateSet("start", "stop", "status")][string]$Command,
  [switch]$Rehearsal,
  [int]$Port = 8000,
  [double]$MinFreeGB = 2.0
)
$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Backend = Join-Path $Repo "backend"
$Python = Join-Path $Backend ".venv\Scripts\python.exe"
$Run = Join-Path $Repo "data\planb"
$PidFile = Join-Path $Run "api.pid"
$Owner = "planb"                                  # label fail-closed.owner=planb on every sandbox
$Image = "fail-closed-sandbox:foundry-v1.8.5"
$Url = "http://127.0.0.1:$Port"

function Free-GB {
  $os = Get-CimInstance Win32_OperatingSystem
  return [math]::Round($os.FreePhysicalMemory / 1MB, 2)  # FreePhysicalMemory is in KB
}

function Sandbox-Ids {
  $ids = docker ps -aq --filter "label=fail-closed.owner=$Owner" 2>$null
  if ($LASTEXITCODE -ne 0) { return @() }
  return @($ids | Where-Object { $_ })
}

function Api-Pid {
  if (-not (Test-Path $PidFile)) { return $null }
  $p = [int](Get-Content $PidFile -Raw)
  if (Get-Process -Id $p -ErrorAction SilentlyContinue) { return $p }
  return $null
}

function Env-Length([string]$name) {
  # length of a value in .env, never the value itself
  $line = Get-Content (Join-Path $Repo ".env") -ErrorAction SilentlyContinue | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
  if (-not $line) { return 0 }
  return ($line.Substring($name.Length + 1)).Trim().Length
}

function Fail([string]$msg) { Write-Host "NOT STARTED: $msg" -ForegroundColor Red; exit 1 }

function Start-PlanB {
  if (Api-Pid) { Write-Host "Already running (pid $(Api-Pid)). Use: .\deploy\planb.ps1 status"; return }

  $free = Free-GB
  Write-Host "free RAM: $free GB (need at least $MinFreeGB GB)"
  if ($free -lt $MinFreeGB) {
    Fail "only $free GB of RAM is free; at least $MinFreeGB GB is needed. Close other programs (browsers, IDEs) and try again."
  }
  docker info --format "{{.ServerVersion}}" *> $null
  if ($LASTEXITCODE -ne 0) { Fail "Docker is not running. Start Docker Desktop, wait until it says 'running', and try again." }
  docker image inspect $Image *> $null
  if ($LASTEXITCODE -ne 0) { Fail "the sandbox image $Image is missing. Build it: .\sandbox\build.ps1" }
  if (-not (Test-Path (Join-Path $Repo "frontend\dist\index.html"))) { Fail "the web UI is not built. Run: cd frontend; npm run build" }
  if (-not (Test-Path $Python)) { Fail "backend\.venv is missing (see README 'Run it locally')." }
  if ((Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) { Fail "port $Port is already in use." }

  if ((Env-Length "VULTR_INFERENCE_API_KEY") -eq 0) {
    Fail "VULTR_INFERENCE_API_KEY is empty in .env. Type it yourself: notepad $Repo\.env"
  }
  $tokenLen = Env-Length "ADMIN_TOKEN"
  if ($tokenLen -lt 32) {
    if (-not $Rehearsal) {
      Fail ("ADMIN_TOKEN in .env is missing or shorter than 32 characters. Set it yourself, in your own terminal:`n" +
            "  1. make one:  backend\.venv\Scripts\python -c `"import secrets; print(secrets.token_hex(32))`"`n" +
            "  2. paste it:  notepad $Repo\.env   (line ADMIN_TOKEN=...)")
    }
    Write-Host "Rehearsal: no ADMIN_TOKEN, so every admin action is disabled (refused with 403)." -ForegroundColor Yellow
  }

  # stale Plan B sandboxes from an earlier run that was not stopped
  $stale = Sandbox-Ids
  if ($stale.Count) { docker rm -f $stale *> $null; Write-Host "removed $($stale.Count) leftover Plan B sandbox(es)" }

  New-Item -ItemType Directory -Force $Run | Out-Null
  # Same as the VM. Process environment wins over .env, so these hold whatever .env says.
  $env:PUBLIC_DEMO = "true"
  $env:LLM_MODE = "live"
  $env:LLM_DAILY_BUDGET_USD = "1"
  $env:LLM_BUDGET_FILE = (Join-Path $Run "llm_budget.json")
  $env:REDTEAM_PUBLIC = "false"
  $env:SANDBOX_MODE = "warm"
  $env:SANDBOX_OWNER = $Owner
  $env:DEPLOY_TARGET = "laptop"                  # page footer: Guard + sandboxes on the demo laptop
  $env:SANDBOX_POOL_SIZE = "2"                    # laptop: 2 warm sandboxes (the VM keeps 4)
  $env:RAM_GUARD_MIN_FREE_GB = "0.75"             # below this, new sandbox work waits, then "Guard busy"
  $env:HISTORY_DB = (Join-Path $Run "history.sqlite3")
  $env:SIGNER_DB = (Join-Path $Run "signer.sqlite3")
  $env:RATE_REPLAYS_PER_MINUTE = "10"
  $env:RATE_SIMULATIONS_PER_10_MINUTES = "2"
  $env:RATE_DEMO_ACTIONS_PER_MINUTE = "30"
  $env:DEMO_IDLE_RESET_MINUTES = "10"
  $env:LIVE_UPDATES = "poll"                     # a Cloudflare quick tunnel does not pass Server-Sent Events
  $env:TRUSTED_PROXY = "cloudflared"              # visitor IP from CF-Connecting-IP, only from 127.0.0.1
  $env:PYTHONUNBUFFERED = "1"

  $p = Start-Process -FilePath $Python -WorkingDirectory $Backend -WindowStyle Hidden -PassThru `
        -ArgumentList "-m", "uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", "$Port", "--workers", "1", "--no-proxy-headers", "--log-level", "warning" `
        -RedirectStandardOutput (Join-Path $Run "api.out.log") -RedirectStandardError (Join-Path $Run "api.err.log")
  Set-Content -Path $PidFile -Value $p.Id
  for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-RestMethod "$Url/health" -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep -Seconds 1 }
    if ($p.HasExited) { break }
  }
  try { $s = Invoke-RestMethod "$Url/status" -TimeoutSec 5 } catch {
    Write-Host "The API did not start. Last lines of $Run\api.err.log:" -ForegroundColor Red
    Get-Content (Join-Path $Run "api.err.log") -Tail 15
    Stop-PlanB; exit 1
  }
  Write-Host "RUNNING: $Url  (pid $($p.Id))" -ForegroundColor Green
  Write-Host ("  public demo: {0} | AI: {1} {2} | budget/day: `${3} | sandbox: {4}" -f `
    $s.public_demo, $s.llm, $s.model, $s.llm_budget.limit_usd, $s.sandbox_mode)
  Write-Host "  Not on the internet. To share it, see docs/deploy.md, Plan B (cloudflared)."
}

function Stop-PlanB {
  $p = Api-Pid
  if ($p) {
    # the venv's python.exe is a launcher with a child interpreter: stop the whole tree
    taskkill /PID $p /T /F *> $null
    Write-Host "stopped the API (pid $p)"
  } else { Write-Host "the API was not running" }
  Remove-Item $PidFile -ErrorAction SilentlyContinue
  # warm-pool refills may still be starting a container: repeat until two quiet passes
  $removed = 0; $quiet = 0
  for ($i = 0; $i -lt 20 -and $quiet -lt 2; $i++) {
    $ids = Sandbox-Ids
    if ($ids.Count) { docker rm -f $ids *> $null; $removed += $ids.Count; $quiet = 0 } else { $quiet++ }
    Start-Sleep -Seconds 1
  }
  Write-Host "removed $removed sandbox container(s); left: $((Sandbox-Ids).Count)"
}

function Status-PlanB {
  $p = Api-Pid
  if ($p) {
    Write-Host "API: running (pid $p) at $Url" -ForegroundColor Green
    try {
      $s = Invoke-RestMethod "$Url/status" -TimeoutSec 5
      Write-Host ("  public demo: {0} | AI: {1} {2} | breaker level: {3}" -f $s.public_demo, $s.llm, $s.model, $s.breaker_level)
      Write-Host ("  AI spend today: `${0} of `${1}{2}" -f $s.llm_budget.spent_today_usd, $s.llm_budget.limit_usd, $(if ($s.llm_budget.reached) { " (REACHED: explanations paused)" } else { "" }))
    } catch { Write-Host "  /status does not answer" -ForegroundColor Yellow }
  } else { Write-Host "API: not running" }
  $listen = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $_.LocalAddress } | Select-Object -Unique)
  if ($listen.Count) {
    $open = @($listen | Where-Object { $_ -ne "127.0.0.1" -and $_ -ne "::1" })
    if ($open.Count) { Write-Host "  WARNING: port $Port listens on $($open -join ', '), not only on 127.0.0.1" -ForegroundColor Red }
    else { Write-Host "  listens on 127.0.0.1 only" }
  }
  Write-Host "sandbox containers (Plan B): $((Sandbox-Ids).Count)"
  Write-Host "free RAM: $(Free-GB) GB"
}

switch ($Command) {
  "start" { Start-PlanB }
  "stop" { Stop-PlanB }
  "status" { Status-PlanB }
}
