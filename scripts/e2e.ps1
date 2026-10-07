<#
.SYNOPSIS
  Run the Playwright E2E suite the one safe way: from apps/web, with the
  project-local Playwright binary and an explicit config.

.DESCRIPTION
  B17 Phase 0b, after environment incident 14 (KNOWN_LIMITATIONS.md):
  `npx playwright test` launched from the repo root fetched Playwright
  1.63.0 instead of the project's 1.62.1, found no config, collected the
  Vitest unit tests under src/, and ran nothing. This launcher removes
  every part of that:
    - sets the working directory to apps/web explicitly;
    - uses apps/web/node_modules/.bin/playwright.cmd, never npx resolution;
    - prints the Playwright version and the resolved config path first;
    - refuses to run if the binary or playwright.config.ts is missing.
  It does not start or check the stack - use scripts/fake-stack.ps1 (or
  scripts/real-stack.ps1 for the single real-provider session) first.
  Extra arguments go straight to `playwright test`.

.EXAMPLE
  scripts\e2e.ps1
#>
# "Continue", not "Stop" - see scripts/fake-stack.ps1 for why; the exit code
# below is checked explicitly.
$ErrorActionPreference = "Continue"
$web = Join-Path (Split-Path -Parent $PSScriptRoot) "apps\web"
$bin = Join-Path $web "node_modules\.bin\playwright.cmd"
$cfg = Join-Path $web "playwright.config.ts"

if (-not (Test-Path $bin)) { throw "refusing: no project-local Playwright at $bin (run npm ci in apps/web)" }
if (-not (Test-Path $cfg)) { throw "refusing: no Playwright config at $cfg" }

Push-Location $web
try {
    Write-Host "cwd:     $((Get-Location).Path)"
    Write-Host "binary:  $bin"
    Write-Host "version: $(& $bin --version)"
    Write-Host "config:  $((Resolve-Path $cfg).Path)"
    & $bin test --config $cfg @args
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code
