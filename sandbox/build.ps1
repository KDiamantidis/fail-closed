# Build the sandbox image on Windows (Docker Desktop). Run from anywhere.
# Docker writes progress to stderr, so we check the exit code instead of ErrorActionPreference=Stop.
# The Dockerfile itself fails the build if solc is in the runtime image or the artifact is missing.
Push-Location $PSScriptRoot
try {
    docker build -t fail-closed-sandbox:foundry-v1.8.5 . 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw "docker build failed" }
    Write-Output "built fail-closed-sandbox:foundry-v1.8.5"
} finally {
    Pop-Location
}
