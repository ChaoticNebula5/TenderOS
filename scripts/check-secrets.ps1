<#
.SYNOPSIS
  Run CI's exact detect-secrets check on a throwaway copy of the tracked
  files, so a shifted baseline line is caught before a push.

.DESCRIPTION
  .secrets.baseline records the line number of every audited finding. Moving
  a flagged line (commit 6 inserted one line above two in ci.yml) makes
  detect-secrets-hook rewrite the baseline and exit 3, which fails CI's
  security job even though no new secret exists. Run in place, the hook
  would also rewrite the real baseline as a side effect.

  This script:
    - copies the working-tree version of every tracked file, filtered exactly
      as ci.yml's detect-secrets step filters them, into a new temp folder;
    - stages them in a throwaway git repo there (the hook refuses an
      unstaged baseline);
    - runs CI's command, `detect-secrets-hook --baseline .secrets.baseline
      <files>`, with this repo's pinned detect-secrets;
    - prints PASS, or the hook's own findings plus every baseline entry
      whose line number moved (file, type, old -> new; never the hash);
    - deletes the temp folder. The real .secrets.baseline is never written.

  Scope: tracked files only, like CI. A new file is checked once it is
  tracked (git add). Windows caps a command line at 32767 characters; the
  file list is passed on one line, as CI does, and the script refuses rather
  than batching once that limit is near.

.EXAMPLE
  scripts\check-secrets.ps1
#>
# "Continue", not "Stop" - see scripts/fake-stack.ps1 for why; every native
# exit code below is checked explicitly.
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
# Same exclusions as ci.yml's detect-secrets step.
$exclude = '\.venv/|apps/web/node_modules/|apps/web/playwright-report/|KNOWN_LIMITATIONS\.md'

$prevEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try {
    $listed = (git -C $repo ls-files -z) -join "" -split "`0"
} finally {
    [Console]::OutputEncoding = $prevEncoding
}
if ($LASTEXITCODE -ne 0) { throw "refusing: git ls-files failed in $repo" }
$files = @($listed | Where-Object { $_ -ne "" -and $_ -notmatch $exclude -and (Test-Path -LiteralPath (Join-Path $repo $_)) })
if ($files -notcontains ".secrets.baseline") { throw "refusing: .secrets.baseline is not tracked in $repo" }

$argLength = ($files | Measure-Object -Property Length -Sum).Sum + 3 * $files.Count
if ($argLength -gt 30000) {
    throw ("refusing: the file list is $argLength characters, too close to Windows' 32767 " +
        "command-line limit to pass in one call as CI does. This check needs batching.")
}

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("tenderos-check-secrets-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    foreach ($f in $files) {
        $dest = Join-Path $tmp $f
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
        Copy-Item -LiteralPath (Join-Path $repo $f) -Destination $dest
    }
    git -C $tmp init -q
    # The hook scans the copied files on disk, not the index, so line-ending
    # conversion warnings from this throwaway staging are noise: silenced.
    git -C $tmp -c core.autocrlf=false -c core.safecrlf=false add -A
    if ($LASTEXITCODE -ne 0) { throw "refusing: could not stage the throwaway copy in $tmp" }

    Write-Host "repo:     $repo"
    Write-Host "copy:     $tmp ($($files.Count) tracked files)"
    Write-Host "command:  detect-secrets-hook --baseline .secrets.baseline <files>  ($(& uv run --project $repo detect-secrets --version))"
    Push-Location $tmp
    try {
        & uv run --project $repo detect-secrets-hook --baseline .secrets.baseline @files
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }

    if ($code -eq 0) {
        Write-Host "PASS"
        exit 0
    }

    # The hook rewrote the copy's baseline (line numbers moved) and/or
    # reported new findings above. List every entry whose line number moved.
    $old = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $repo ".secrets.baseline") | ConvertFrom-Json
    $new = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $tmp ".secrets.baseline") | ConvertFrom-Json
    $moved = @()
    foreach ($file in $old.results.PSObject.Properties) {
        $after = $new.results.PSObject.Properties[$file.Name]
        if ($null -eq $after) { continue }
        foreach ($entry in $file.Value) {
            $match = $after.Value | Where-Object { $_.hashed_secret -eq $entry.hashed_secret -and $_.type -eq $entry.type }
            foreach ($m in @($match)) {
                if ($m.line_number -ne $entry.line_number) {
                    $moved += "  $($file.Name)  [$($entry.type)]  line $($entry.line_number) -> $($m.line_number)"
                }
            }
        }
    }
    Write-Host "FAIL (detect-secrets-hook exit $code)"
    if ($moved.Count -gt 0) {
        Write-Host "baseline entries whose line numbers moved:"
        $moved | ForEach-Object { Write-Host $_ }
        Write-Host "Refresh .secrets.baseline line numbers in the same commit as the change that moved them."
    } else {
        Write-Host "no line numbers moved; see the hook's findings above."
    }
    exit 1
} finally {
    Remove-Item -Recurse -Force -LiteralPath $tmp -ErrorAction SilentlyContinue
}
