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
    2. builds and force-recreates migrate, api, web, the four workers
       and outbox_dispatcher; migrate runs alembic to head
       and the app services start only once it has exited 0 (compose's
       service_completed_successfully), so a failed migration fails here,
    3. waits for the api health check,
    4. prints `printenv LLM_PROVIDER` for api, the four workers and
       outbox_dispatcher,
       and fails unless all four say fake.
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

    # --remove-orphans: a container of a service no longer in the compose files
    # (the single `worker` before plan item 44) would otherwise keep running and
    # consume every queue it was started with.
    & docker compose @files up -d --build --force-recreate --remove-orphans migrate api web workflow_worker ingestion_worker maintenance_worker operations_worker outbox_dispatcher
    if ($LASTEXITCODE -ne 0) { throw "compose up failed (a failed migration stops it here too: docker compose logs migrate)" }
    $migrateExit = (& docker inspect -f "{{.State.ExitCode}}" tenderos-migrate-1).Trim()
    Write-Host "migrate: exit $migrateExit"

    $healthy = $false
    for ($i = 0; $i -lt 60; $i++) {
        $status = (& docker inspect -f "{{.State.Health.Status}}" tenderos-api-1).Trim()
        if ($status -eq "healthy") { $healthy = $true; break }
        Start-Sleep -Seconds 3
    }
    if (-not $healthy) { throw "api never became healthy" }

    $bad = @()
    foreach ($svc in @("api", "workflow_worker", "ingestion_worker", "maintenance_worker", "operations_worker", "outbox_dispatcher")) {
        $provider = (& docker compose @files exec -T $svc printenv LLM_PROVIDER | Out-String).Trim()
        Write-Host "${svc}: LLM_PROVIDER=$provider"
        if ($provider -ne "fake") { $bad += $svc }
    }
    if ($bad.Count -gt 0) { throw "NOT a fake stack - LLM_PROVIDER is not fake in: $($bad -join ', ')" }
    Write-Host "fake stack up: every LLM call is answered by FakeLLMClient (label all results fake)."
} finally {
    Pop-Location
}
