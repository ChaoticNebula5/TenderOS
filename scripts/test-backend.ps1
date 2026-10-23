<#
.SYNOPSIS
  Run the backend test suite with `make test`'s exact environment.

.DESCRIPTION
  B17 Phase 0. `make` is not installed on this machine, so the Makefile's
  `test` target could not be run here, and its environment was retyped by
  hand each time. Both this script and the Makefile now read the same file,
  scripts/test-backend.env, so the environment is defined once.

  Guards, all checked before pytest starts:
    - POSTGRES_DSN must end in _test (tests/integration/conftest.py checks
      this too; failing here is earlier and louder).
    - REDIS_URL must be database 1 (the running stack's RQ uses 0).
    - The server behind POSTGRES_DSN must report PostgreSQL 16.x (another
      server on the port fails here, before pytest).
    - The stack's api, worker, operations_worker and outbox_dispatcher
      containers must be stopped: six fixtures run `MATCH (n) DETACH DELETE n` against the one
      shared Neo4j (KNOWN_LIMITATIONS.md). -AllowRunningStack overrides it.
    - Both Hugging Face models the code loads by name (ingestion/embed.py,
      agents/knowledge/rerank.py) must be in the local cache. The run is
      offline (HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1), as the images are
      (decision 0018), so a missing model fails here, with the command to
      fetch it, instead of being downloaded in the middle of a test.

  Any further arguments are passed straight to pytest, e.g.
    scripts\test-backend.ps1 tests/unit -q
#>
# A plain (non-advanced) script on purpose: everything not bound to
# -AllowRunningStack lands in $args unchanged, including pytest's own
# dash flags (-q, -p, -k), which an advanced script would try to bind.
param([switch]$AllowRunningStack)
$PytestArgs = $args

# "Continue", not "Stop": in Windows PowerShell 5.1, "Stop" turns any
# stderr line from a native program (docker prints ordinary progress
# there) into a terminating NativeCommandError when stderr is redirected.
# Every native call below is checked through $LASTEXITCODE and every
# refusal is an explicit throw, so nothing relies on "Stop".
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $PSScriptRoot "test-backend.env"

# Load KEY=VALUE lines, skipping comments and blanks.
$vars = @{}
foreach ($line in Get-Content $envFile) {
    $t = $line.Trim()
    if ($t -eq "" -or $t.StartsWith("#")) { continue }
    $i = $t.IndexOf("=")
    $vars[$t.Substring(0, $i)] = $t.Substring($i + 1)
}

if (-not $vars["POSTGRES_DSN"].EndsWith("_test")) {
    throw "refusing: POSTGRES_DSN in $envFile does not end in _test: $($vars['POSTGRES_DSN'])"
}
if (-not $vars["REDIS_URL"].EndsWith("/1")) {
    throw "refusing: REDIS_URL in $envFile is not database 1: $($vars['REDIS_URL'])"
}

if (-not $AllowRunningStack) {
    Push-Location $repo
    try {
        $running = docker compose ps --status running --format "{{.Service}}"
    } finally {
        Pop-Location
    }
    $apps = @($running | Where-Object { $_ -in @("api", "worker", "operations_worker", "outbox_dispatcher") })
    if ($apps.Count -gt 0) {
        throw ("refusing: stack app containers are running ($($apps -join ', ')). pytest wipes " +
            "the shared Neo4j and must not race a live worker. Stop them first, e.g. " +
            "docker compose stop api worker operations_worker outbox_dispatcher web, or pass -AllowRunningStack.")
    }
}

# The model names are read from the code itself, so this check cannot drift
# from what the tests load. The cache layout is huggingface_hub's:
# <cache>/models--<org>--<name>/snapshots/<revision>/config.json, with <cache>
# from HF_HUB_CACHE, else HF_HOME\hub, else ~\.cache\huggingface\hub.
$modelFiles = @("packages/tenderos_backend/src/tenderos_backend/ingestion/embed.py",
                "packages/tenderos_backend/src/tenderos_backend/agents/knowledge/rerank.py")
$hfCache = if ($env:HF_HUB_CACHE) { $env:HF_HUB_CACHE }
           elseif ($env:HF_HOME) { Join-Path $env:HF_HOME "hub" }
           else { Join-Path $env:USERPROFILE ".cache\huggingface\hub" }
foreach ($mf in $modelFiles) {
    $match = Select-String -LiteralPath (Join-Path $repo $mf) -Pattern '^_MODEL_NAME = "([^"]+)"'
    if (-not $match) { throw "refusing: no _MODEL_NAME line found in $mf; this check needs updating" }
    $model = $match.Matches[0].Groups[1].Value
    $snapshots = Join-Path (Join-Path $hfCache ("models--" + ($model -replace '/', '--'))) "snapshots"
    $cached = @(Get-ChildItem -LiteralPath $snapshots -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "config.json") })
    if ($cached.Count -eq 0) {
        throw ("refusing: model $model is not in the Hugging Face cache ($hfCache), and tests run offline. " +
            "Fetch it once with: uv run python -c `"from tenderos_backend.ingestion.embed import _get_model as e; " +
            "from tenderos_backend.agents.knowledge.rerank import _get_model as r; e(); r()`"")
    }
}
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"

# Windows deletes an environment variable that is set to an empty string, so
# an empty value here (the blanked Gemini keys) would vanish and pydantic would
# fall back to the real key in the untracked .env. A non-empty, clearly invalid
# sentinel keeps the .env value out: any accidental real call is rejected as
# an invalid key, at no cost. (The bash path, used by the Makefile and CI,
# keeps genuinely empty values and needs no sentinel.)
foreach ($k in $vars.Keys) {
    $v = $vars[$k]
    if ($v -eq "") { $v = "disabled-in-tests" }
    Set-Item -Path "Env:$k" -Value $v
}

# The server behind POSTGRES_DSN must be the project's PostgreSQL 16, not
# whatever else answers on that port: on this host a native PostgreSQL 18
# once held localhost:5432 and the suite ran against it (KNOWN_LIMITATIONS.md,
# environment traps, incident 2). The compose stack now publishes 5433; this
# asks the server itself. psycopg (sync) keeps the probe to one line; single
# quotes only, because Windows PowerShell 5.1 mangles embedded double quotes
# in native arguments.
Push-Location $repo
try {
    $probe = "import os, psycopg; c = psycopg.connect(os.environ['POSTGRES_DSN'].replace('postgresql+asyncpg://', 'postgresql://', 1), connect_timeout=10); print(c.info.server_version); c.close()"
    $serverVersion = (& uv run --frozen python -c $probe 2>&1 | Out-String).Trim()
    $probeCode = $LASTEXITCODE
} finally {
    Pop-Location
}
$dbHost = ($vars["POSTGRES_DSN"] -split "@")[-1].Split("/")[0]
if ($probeCode -ne 0 -or $serverVersion -notmatch '^\d+$') {
    throw "refusing: could not ask the Postgres server at $dbHost for its version. Is the stack's postgres running (docker compose start postgres)? Output: $serverVersion"
}
$pgMajor = [int]([int]$serverVersion / 10000)
$pgLabel = "$pgMajor.$([int]$serverVersion % 10000)"
if ($pgMajor -ne 16) {
    throw "refusing: the Postgres server at $dbHost reports version $pgLabel, not 16.x. Another server holds that port; the project's container publishes 5433 (docker-compose.yml)."
}

Write-Host "test env from $envFile (POSTGRES_DSN=...$($vars['POSTGRES_DSN'].Split('/')[-1]) on $dbHost, PostgreSQL $pgLabel, REDIS_URL=$($vars['REDIS_URL']), HF offline, cache $hfCache)"

Push-Location $repo
try {
    & uv run pytest @PytestArgs
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code
