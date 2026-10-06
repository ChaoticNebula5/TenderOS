<#
.SYNOPSIS
  Bring up the fake-provider E2E stack, the one way it should ever be started.

.DESCRIPTION
  B17 Phase 0. Hardcodes the three-file compose list, because a partial
  `docker compose` command silently drops every -f override and the
  containers come up healthy with the wrong configuration
  (KNOWN_LIMITATIONS.md, environment-traps incident 13). Then:
    1. starts redis and flushes it (orphaned scheduled RQ jobs from earlier
       runs otherwise fire in one burst on the next worker start - incident
       12),
    2. builds and force-recreates api, web, worker and outbox_dispatcher,
    3. waits for the api health check,
    4. prints `printenv LLM_PROVIDER` for api, worker and outbox_dispatcher,
       and fails unless all three say fake.
  No behaviour change to the stack itself: this only runs the commands that
  were previously typed by hand.
#>
# "Continue", not "Stop": in Windows PowerShell 5.1, "Stop" turns any
# stderr line from a native program (docker prints ordinary progress
# there) into a terminating NativeCommandError when stderr is redirected.
# Every native call below is checked through $LASTEXITCODE and every
# refusal is an explicit throw, so nothing relies on "Stop".
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
$files = @("-f", "docker-compose.yml", "-f", "docker-compose.e2e.yml", "-f", "docker-compose.e2e-fake.yml")

Push-Location $repo
try {
    Write-Host "compose: docker compose $($files -join ' ')"

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

    $bad = @()
    foreach ($svc in @("api", "worker", "outbox_dispatcher")) {
        $provider = (& docker compose @files exec -T $svc printenv LLM_PROVIDER | Out-String).Trim()
        Write-Host "${svc}: LLM_PROVIDER=$provider"
        if ($provider -ne "fake") { $bad += $svc }
    }
    if ($bad.Count -gt 0) { throw "NOT a fake stack - LLM_PROVIDER is not fake in: $($bad -join ', ')" }
    Write-Host "fake stack up: every LLM call is answered by FakeLLMClient (label all results fake)."
} finally {
    Pop-Location
}
