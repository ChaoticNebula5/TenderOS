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
    - prints PASS, or the hook's own output plus every baseline entry
      REMOVED (no longer found), ADDED (a new finding) and MOVED (line
      number changed), each by file, detector type and line - never a hash
      or a value;
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
        # Captured (and echoed in full) so new findings can be listed below. The
        # hook prints type and location only, never the value.
        $hookOutput = @(& uv run --project $repo detect-secrets-hook --baseline .secrets.baseline @files 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
        $hookOutput | ForEach-Object { Write-Host $_ }
    } finally {
        Pop-Location
    }

    if ($code -eq 0) {
        Write-Host "PASS"
        exit 0
    }

    # The hook rewrote the copy's baseline and/or reported new findings above.
    # Compare the two baselines entry by entry. An entry is identified by
    # (file, detector type, hash); the hash is used only to pair entries and
    # is never printed. Entries sharing a key are paired in line order, so a
    # count difference shows up as REMOVED or ADDED, not as a bogus MOVED.
    $old = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $repo ".secrets.baseline") | ConvertFrom-Json
    $new = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $tmp ".secrets.baseline") | ConvertFrom-Json
    function Get-BaselineLines($baseline) {
        $byKey = @{}
        foreach ($file in $baseline.results.PSObject.Properties) {
            foreach ($entry in @($file.Value)) {
                $key = "$($file.Name)`t$($entry.type)`t$($entry.hashed_secret)"
                if (-not $byKey.ContainsKey($key)) { $byKey[$key] = New-Object System.Collections.Generic.List[int] }
                $byKey[$key].Add([int]$entry.line_number)
            }
        }
        return $byKey
    }
    $oldLines = Get-BaselineLines $old
    $newLines = Get-BaselineLines $new
    $removed = @(); $added = @(); $moved = @()
    # A new finding is never written into the baseline: the hook only reports
    # it ("Secret Type: X" then "Location: file:line"), so ADDED comes from
    # its output. Baseline-only additions are collected below as well.
    $pendingType = $null
    foreach ($line in $hookOutput) {
        if ($line -match '^\s*Secret Type:\s*(.+?)\s*$') { $pendingType = $Matches[1] }
        elseif ($line -match '^\s*Location:\s*(.+):(\d+)\s*$' -and $pendingType) {
            $added += "  $($Matches[1])  [$pendingType]  line $($Matches[2])"
            $pendingType = $null
        }
    }
    foreach ($key in (@($oldLines.Keys) + @($newLines.Keys) | Sort-Object -Unique)) {
        $file, $type, $null = $key -split "`t"
        $before = @(if ($oldLines.ContainsKey($key)) { $oldLines[$key] | Sort-Object })
        $after = @(if ($newLines.ContainsKey($key)) { $newLines[$key] | Sort-Object })
        $paired = [Math]::Min($before.Count, $after.Count)
        for ($i = 0; $i -lt $paired; $i++) {
            if ($before[$i] -ne $after[$i]) { $moved += "  $file  [$type]  line $($before[$i]) -> $($after[$i])" }
        }
        for ($i = $paired; $i -lt $before.Count; $i++) { $removed += "  $file  [$type]  line $($before[$i])" }
        for ($i = $paired; $i -lt $after.Count; $i++) { $added += "  $file  [$type]  line $($after[$i])" }
    }
    Write-Host "FAIL (detect-secrets-hook exit $code)"
    foreach ($section in @(@("REMOVED (in the baseline, no longer found)", $removed),
                           @("ADDED (found, not in the baseline)", $added),
                           @("MOVED (line number changed)", $moved))) {
        if ($section[1].Count -gt 0) {
            Write-Host "$($section[0]):"
            $section[1] | ForEach-Object { Write-Host $_ }
        }
    }
    if ($removed.Count + $added.Count + $moved.Count -eq 0) {
        Write-Host "the baseline entries did not change; see the hook's findings above."
    } else {
        Write-Host ("Review each entry. ADDED: remove the secret, or record an audited false positive in " +
            ".secrets.baseline. REMOVED/MOVED: refresh .secrets.baseline in the same commit as the change that caused it.")
    }
    exit 1
} finally {
    Remove-Item -Recurse -Force -LiteralPath $tmp -ErrorAction SilentlyContinue
}
