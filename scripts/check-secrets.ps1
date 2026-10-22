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
    - copies the working-tree version of the files to check, filtered
      exactly as ci.yml's detect-secrets step filters them, plus
      .secrets.baseline, into a new temp folder;
    - stages them in a throwaway git repo there (the hook refuses an
      unstaged baseline);
    - runs CI's command, `detect-secrets-hook --baseline .secrets.baseline
      <files>`, with this repo's pinned detect-secrets, in batches that each
      fit under Windows' command-line limit (see below);
    - prints PASS, or the hook's own output plus every baseline entry
      REMOVED (no longer found), ADDED (a new finding) and MOVED (line
      number changed), each by file, detector type and line - never a hash
      or a value;
    - deletes the temp folder. The real .secrets.baseline is never written:
      its SHA-256 is compared before and after, and a difference fails the
      run.

  Batching. Windows caps a command line at 32767 characters, and the full
  tracked-file list is longer than that. CI passes the list in one call on
  Linux. Here it is split into batches of at most -MaxChars characters each,
  all run against the same copy. The hook only compares the files it is
  given with their own baseline entries, so batches give the same verdict as
  one call, with one catch handled here: a batch that moves a line rewrites
  the copy's baseline (exit 3), and the next batch would then refuse with
  "baseline file is unstaged" (exit 1) for the wrong reason. The copy's
  baseline is therefore re-staged after every batch. The baseline
  comparison at the end covers every batch's rewrites.

  Scope: tracked files only, like CI, unless -Files is given. A new file is
  checked by a full run once it is tracked (git add).

.PARAMETER Files
  Check only these files (paths relative to the repo root), for example the
  files a commit changes. Each must exist; CI's exclusions still apply, and
  an excluded file is listed and skipped. Replaces chunking the list by hand.

.PARAMETER MaxChars
  Largest file-list length per hook call, in characters. Default 30000,
  below the 32767 limit. Lower it to force several batches.

.PARAMETER SelfTest
  Proves the check can fail: plants one file holding a fake private-key
  header in the throwaway copy only (never in the repo), adds it to the
  last batch, and expects the run to FAIL naming that file. Exits 0 when it
  does, 1 when it does not.

.EXAMPLE
  scripts\check-secrets.ps1

.EXAMPLE
  scripts\check-secrets.ps1 -Files ci.yml,scripts\check-secrets.ps1

.EXAMPLE
  scripts\check-secrets.ps1 -MaxChars 5000 -SelfTest
#>
param(
    [string[]]$Files,
    [int]$MaxChars = 30000,
    [switch]$SelfTest
)
# "Continue", not "Stop" - see scripts/fake-stack.ps1 for why; every native
# exit code below is checked explicitly.
$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
# Same exclusions as ci.yml's detect-secrets step.
$exclude = '\.venv/|apps/web/node_modules/|apps/web/playwright-report/|KNOWN_LIMITATIONS\.md'
$baselineName = ".secrets.baseline"
$plantedName = "check-secrets-selftest/planted.txt"

if ($MaxChars -lt 200) { throw "refusing: -MaxChars $MaxChars is too small to hold one path" }

if ($Files) {
    $candidates = @($Files | ForEach-Object { ($_ -replace '\\', '/') -replace '^\./', '' } | Sort-Object -Unique)
    $missing = @($candidates | Where-Object { -not (Test-Path -LiteralPath (Join-Path $repo $_) -PathType Leaf) })
    if ($missing.Count -gt 0) { throw "refusing: not a file under ${repo}: $($missing -join ', ')" }
    $skipped = @($candidates | Where-Object { $_ -match $exclude })
    foreach ($s in $skipped) { Write-Host "skipped (excluded in CI too): $s" }
    $files = @($candidates | Where-Object { $_ -notmatch $exclude })
} else {
    $prevEncoding = [Console]::OutputEncoding
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    try {
        $listed = (git -C $repo ls-files -z) -join "" -split "`0"
    } finally {
        [Console]::OutputEncoding = $prevEncoding
    }
    if ($LASTEXITCODE -ne 0) { throw "refusing: git ls-files failed in $repo" }
    if ($listed -notcontains $baselineName) { throw "refusing: $baselineName is not tracked in $repo" }
    $files = @($listed | Where-Object { $_ -ne "" -and $_ -notmatch $exclude -and (Test-Path -LiteralPath (Join-Path $repo $_)) })
}
if ($files.Count -eq 0 -and -not $SelfTest) { throw "refusing: no files left to check" }

# Batches: consecutive files whose list length (each path plus 3 characters
# for quoting and a separator, the same estimate as before batching) stays
# under -MaxChars.
$scanList = @($files)
if ($SelfTest) { $scanList += $plantedName }
$batches = New-Object System.Collections.Generic.List[object]
$current = New-Object System.Collections.Generic.List[string]
$length = 0
foreach ($f in $scanList) {
    $cost = $f.Length + 3
    if ($cost -gt $MaxChars) { throw "refusing: one path is longer than -MaxChars ($MaxChars): $f" }
    if ($length + $cost -gt $MaxChars -and $current.Count -gt 0) {
        $batches.Add($current.ToArray())
        $current = New-Object System.Collections.Generic.List[string]
        $length = 0
    }
    $current.Add($f)
    $length += $cost
}
if ($current.Count -gt 0) { $batches.Add($current.ToArray()) }

$realBaseline = Join-Path $repo $baselineName
$baselineHashBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $realBaseline).Hash

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("tenderos-check-secrets-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    foreach ($f in (@($files) + @($baselineName) | Sort-Object -Unique)) {
        $dest = Join-Path $tmp $f
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
        Copy-Item -LiteralPath (Join-Path $repo $f) -Destination $dest
    }
    if ($SelfTest) {
        # Built from pieces so this script itself holds no private-key header
        # for the scan to find.
        $dest = Join-Path $tmp $plantedName
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
        $header = "-----BEGIN " + "RSA PRIVATE" + " KEY-----"
        Set-Content -LiteralPath $dest -Encoding ascii -Value @($header, "selftest, not a real key", ("-----END " + "RSA PRIVATE" + " KEY-----"))
    }
    git -C $tmp init -q
    # The hook scans the copied files on disk, not the index, so line-ending
    # conversion warnings from this throwaway staging are noise: silenced.
    git -C $tmp -c core.autocrlf=false -c core.safecrlf=false add -A
    if ($LASTEXITCODE -ne 0) { throw "refusing: could not stage the throwaway copy in $tmp" }

    Write-Host "repo:     $repo"
    Write-Host "copy:     $tmp ($($scanList.Count) files to scan, $($batches.Count) batch(es) of at most $MaxChars characters)"
    if ($SelfTest) { Write-Host "selftest: planted $plantedName in the copy only" }
    Write-Host "command:  detect-secrets-hook --baseline $baselineName <files>  ($(& uv run --project $repo detect-secrets --version))"

    $hookOutput = New-Object System.Collections.Generic.List[string]
    $codes = @()
    Push-Location $tmp
    try {
        for ($b = 0; $b -lt $batches.Count; $b++) {
            $batch = $batches[$b]
            # Captured (and echoed in full) so new findings can be listed below. The
            # hook prints type and location only, never the value.
            $out = @(& uv run --project $repo detect-secrets-hook --baseline $baselineName @batch 2>&1 | ForEach-Object { "$_" })
            $code = $LASTEXITCODE
            $codes += $code
            Write-Host "batch $($b + 1)/$($batches.Count): $($batch.Count) files, exit $code"
            $out | ForEach-Object { Write-Host $_; $hookOutput.Add($_) }
            # A rewritten copy baseline must be staged again, or the next
            # batch refuses it as unstaged (see the description).
            git -C $tmp -c core.autocrlf=false -c core.safecrlf=false add -- $baselineName
            if ($LASTEXITCODE -ne 0) { throw "refusing: could not re-stage the copy's baseline after batch $($b + 1)" }
        }
    } finally {
        Pop-Location
    }

    $baselineHashAfter = (Get-FileHash -Algorithm SHA256 -LiteralPath $realBaseline).Hash
    if ($baselineHashAfter -ne $baselineHashBefore) {
        throw "the real $baselineName changed during the check; it must never be written. Restore it from git."
    }
    Write-Host "real baseline unchanged (SHA-256 compared before and after)"

    $failed = @($codes | Where-Object { $_ -ne 0 }).Count -gt 0
    if (-not $failed) {
        if ($SelfTest) {
            Write-Host "SELFTEST FAILED: the planted file was not reported; this check would miss a real secret."
            exit 1
        }
        Write-Host "PASS"
        exit 0
    }

    # The hook rewrote the copy's baseline and/or reported new findings above.
    # Compare the two baselines entry by entry. An entry is identified by
    # (file, detector type, hash); the hash is used only to pair entries and
    # is never printed. Entries sharing a key are paired in line order, so a
    # count difference shows up as REMOVED or ADDED, not as a bogus MOVED.
    $old = Get-Content -Raw -Encoding UTF8 -LiteralPath $realBaseline | ConvertFrom-Json
    $new = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $tmp $baselineName) | ConvertFrom-Json
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
    Write-Host "FAIL (detect-secrets-hook exit codes per batch: $($codes -join ', '))"
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
    if ($SelfTest) {
        # The hook prints Windows paths (backslashes); compare with slashes.
        $planted = [regex]::Escape($plantedName)
        $named = @($added | Where-Object { ($_ -replace '\\', '/') -match $planted }).Count -gt 0
        $others = @($added | Where-Object { ($_ -replace '\\', '/') -notmatch $planted }) + $removed + $moved
        if ($named -and $others.Count -eq 0) {
            Write-Host "SELFTEST PASSED: the planted file was reported by name, and nothing else changed."
            exit 0
        }
        Write-Host "SELFTEST FAILED: planted file reported: $named; other findings: $($others.Count)."
        exit 1
    }
    exit 1
} finally {
    Remove-Item -Recurse -Force -LiteralPath $tmp -ErrorAction SilentlyContinue
}
