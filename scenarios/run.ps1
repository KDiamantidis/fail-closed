# Run the scenarios on Windows (Docker Desktop).
#   .\scenarios\run.ps1                    # run-all: every scenario, one table + breaker state
#   .\scenarios\run.ps1 d_delegation_drain     # one scenario with the full report
param([string]$Scenario = "")
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

$cli = Join-Path $root "backend\cli.py"
if ($Scenario -eq "") {
    & $py $cli run-all
} else {
    $file = Join-Path $PSScriptRoot ($Scenario -replace '\.json$', '')
    & $py $cli scenario "$file.json"
}
exit $LASTEXITCODE
