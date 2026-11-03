# Run a scenario end to end on Windows (Docker Desktop).
#   .\scenarios\run.ps1                 # 7702_attack
#   .\scenarios\run.ps1 7702_attack -Json
param(
    [string]$Scenario = "7702_attack",
    [switch]$Json
)
$root = Split-Path $PSScriptRoot -Parent
$venv = Join-Path $root "backend\.venv"
$py = Join-Path $venv "Scripts\python.exe"

docker image inspect fail-closed-sandbox:foundry-v1.8.5 *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Output "Sandbox image missing. Build it first: .\sandbox\build.ps1"
    exit 1
}
if (-not (Test-Path $py)) {
    Write-Output "Creating backend\.venv ..."
    python -m venv $venv
    & $py -m pip install -q -e (Join-Path $root "backend")
    if ($LASTEXITCODE -ne 0) { exit 1 }
}

$cliArgs = @((Join-Path $root "backend\cli.py"), "scenario", (Join-Path $PSScriptRoot $Scenario))
if ($Json) { $cliArgs += "--json" }
& $py @cliArgs
exit $LASTEXITCODE
