<#
.SYNOPSIS
  Start and check the observability profile: metrics_exporter, Prometheus
  and Grafana. Dot-sourced by scripts\fake-stack.ps1 and scripts\real-stack.ps1
  (their -Observability switch), so both stacks start and check it the same way.

.DESCRIPTION
  Moved out of fake-stack.ps1 for PR 34 (the real Gemini run needs the same
  services on the live stack); the steps are unchanged:
    1. builds and force-recreates metrics_exporter, prometheus and grafana
       with the caller's compose files;
    2. waits for Prometheus's /-/ready;
    3. requires metrics_exporter still running 5 s later (it refuses at start
       without METRICS_EXPORTER_DB_PASSWORD);
    4. waits for Grafana's /api/health, then asks each provisioned datasource's
       own health check from inside the container with its own admin
       variables, so the admin password never reaches this shell.
  None of the three services calls an LLM.

.EXAMPLE
  . (Join-Path $PSScriptRoot "observability-stack.ps1")
  Start-ObservabilityStack -ComposeArgs @("-f", "docker-compose.yml")
#>

function Start-ObservabilityStack {
    param([Parameter(Mandatory = $true)][string[]]$ComposeArgs)

    & docker compose @ComposeArgs up -d --build --force-recreate metrics_exporter prometheus grafana
    if ($LASTEXITCODE -ne 0) { throw "the observability services did not start" }
    $ready = $false
    for ($i = 0; $i -lt 20; $i++) {
        try {
            $answer = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri "http://127.0.0.1:9090/-/ready"
            if ($answer.StatusCode -eq 200) { $ready = $true; break }
        } catch { }
        Start-Sleep -Seconds 3
    }
    if (-not $ready) { throw "Prometheus never became ready (docker compose logs prometheus)" }
    # The exporter refuses at start without its password: give it time to
    # exit, then require it still running.
    Start-Sleep -Seconds 5
    $state = (& docker inspect -f "{{.State.Status}}" tenderos-metrics_exporter-1).Trim()
    if ($state -ne "running") { throw "metrics_exporter is $state, not running (docker compose logs metrics_exporter; is METRICS_EXPORTER_DB_PASSWORD set?)" }
    # Observability PR 32: Grafana, then each provisioned datasource's own
    # health check.
    $grafanaUp = $false
    for ($i = 0; $i -lt 20; $i++) {
        try {
            $answer = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri "http://127.0.0.1:3000/api/health"
            if ($answer.StatusCode -eq 200) { $grafanaUp = $true; break }
        } catch { }
        Start-Sleep -Seconds 3
    }
    if (-not $grafanaUp) { throw "Grafana never became ready (docker compose logs grafana)" }
    # No double quotes in the script: Windows PowerShell 5.1 does not
    # escape them when passing an argument to a native program.
    foreach ($uid in @("prometheus", "postgres-observability")) {
        $check = 'A=$(printf %s:%s $GF_SECURITY_ADMIN_USER $GF_SECURITY_ADMIN_PASSWORD | base64 | tr -d ''\n''); wget -qO- --header ''Authorization: Basic ''$A http://127.0.0.1:3000/api/datasources/uid/' + $uid + '/health'
        $health = (& docker compose @ComposeArgs exec -T grafana sh -c $check 2>&1 | Out-String).Trim()
        if ($health -notmatch '"status":\s*"OK"') {
            throw "Grafana's $uid datasource is not healthy: $health (for postgres-observability: is GRAFANA_DB_PASSWORD set, and did migrate set up its role?)"
        }
    }
    Write-Host "observability up: Prometheus on http://127.0.0.1:9090 (the api and metrics_exporter, the alert rules), Grafana on http://127.0.0.1:3000 (both datasources healthy)."
}
