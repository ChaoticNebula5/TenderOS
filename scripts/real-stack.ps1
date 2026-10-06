<#
.SYNOPSIS
  Pre-flight launcher for the single real-provider (Phase 3R) session.

.DESCRIPTION
  B17 Phase 0. Starting this stack is what makes LLM calls cost real money,
  so it refuses rather than guesses:
    - refuses if docker-compose.e2e-fake.yml (or any file named *fake*) is
      in the compose file list;
    - resolves the merged config (`docker compose config`) and refuses if
      LLM_PROVIDER for api, worker or outbox_dispatcher is fake, missing,
      or anything other than gemini;
    - reports whether each Gemini key is present (length only, never the
      value).
  It prints a loud banner with the resolved provider and the exact compose
  command before doing anything. With -DryRun it stops there: nothing is
  started. Without it, it flushes Redis (a stale queued job picked up by a
  real-provider worker would spend budget), starts the stack, waits for the
  api health check, and re-checks `printenv LLM_PROVIDER` in all three
  running containers.
  No behaviour change to the stack itself.

.EXAMPLE
  scripts\real-stack.ps1 -DryRun
#>
param(
    [string[]]$ComposeFiles = @("docker-compose.yml", "docker-compose.e2e.yml"),
    [switch]$DryRun
)

# "Continue", not "Stop": in Windows PowerShell 5.1, "Stop" turns any
# stderr line from a native program (docker prints ordinary progress
# there) into a terminating NativeCommandError when stderr is redirected.
# Every native call below is checked through $LASTEXITCODE and every
# refusal is an explicit throw, so nothing relies on "Stop".
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot

$fakeFiles = @($ComposeFiles | Where-Object { $_ -match "fake" })
if ($fakeFiles.Count -gt 0) {
    throw "REFUSING: a fake overlay is in the compose file list: $($fakeFiles -join ', ')"
}
$files = @()
foreach ($f in $ComposeFiles) { $files += @("-f", $f) }
$services = @("api", "worker", "outbox_dispatcher")

Push-Location $repo
try {
    $configJson = & docker compose @files config --format json
    if ($LASTEXITCODE -ne 0) { throw "docker compose config failed" }
    $config = ($configJson | Out-String) | ConvertFrom-Json

    $resolved = @{}
    $keyReport = @()
    foreach ($svc in $services) {
        $envBlock = $config.services.$svc.environment
        $provider = $null
        if ($envBlock -ne $null) { $provider = $envBlock.LLM_PROVIDER }
        $resolved[$svc] = $provider
        foreach ($k in @("GEMINI_API_KEY", "COMPLIANCE_GEMINI_API_KEY", "TALENT_GEMINI_API_KEY", "COMMERCIAL_GEMINI_API_KEY")) {
            $v = $null
            if ($envBlock -ne $null) { $v = $envBlock.$k }
            $len = 0
            if ($v -ne $null) { $len = ([string]$v).Length }
            $keyReport += "  ${svc}.${k}: " + $(if ($len -gt 0) { "present ($len chars)" } else { "EMPTY" })
        }
    }

    $bad = @($services | Where-Object { $resolved[$_] -ne "gemini" })
    $command = "docker compose $($files -join ' ') up -d --build --force-recreate api web worker outbox_dispatcher"

    Write-Host ""
    Write-Host "################################################################" -ForegroundColor Red
    Write-Host "##  REAL-PROVIDER STACK - LLM CALLS COST REAL MONEY            ##" -ForegroundColor Red
    Write-Host "################################################################" -ForegroundColor Red
    foreach ($svc in $services) { Write-Host ("  {0,-18} LLM_PROVIDER = {1}" -f $svc, $resolved[$svc]) }
    $keyReport | ForEach-Object { Write-Host $_ }
    Write-Host "  command: $command"
    Write-Host "################################################################" -ForegroundColor Red
    Write-Host ""

    if ($bad.Count -gt 0) {
        throw "REFUSING: LLM_PROVIDER does not resolve to gemini for: $($bad -join ', ')"
    }
    if ($DryRun) {
        Write-Host "-DryRun: all checks passed; nothing was started."
        return
    }

    & docker compose @files up -d redis
    if ($LASTEXITCODE -ne 0) { throw "redis did not start" }
    & docker compose @files exec -T redis redis-cli FLUSHALL
    if ($LASTEXITCODE -ne 0) { throw "redis FLUSHALL failed" }

    & docker compose @files up -d --build --force-recreate api web worker outbox_dispatcher
    if ($LASTEXITCODE -ne 0) { throw "compose up failed" }

    $healthy = $false
    for ($i = 0; $i -lt 60; $i++) {
        $status = (& docker inspect -f "{{.State.Health.Status}}" tenderos-api-1).Trim()
        if ($status -eq "healthy") { $healthy = $true; break }
        Start-Sleep -Seconds 3
    }
    if (-not $healthy) { throw "api never became healthy" }

    foreach ($svc in $services) {
        $p = (& docker compose @files exec -T $svc printenv LLM_PROVIDER | Out-String).Trim()
        Write-Host "${svc}: LLM_PROVIDER=$p"
        if ($p -ne "gemini") { throw "running container $svc reports LLM_PROVIDER=$p - stop the stack now" }
    }
    Write-Host "real-provider stack up. Label every number from it real."
} finally {
    Pop-Location
}
