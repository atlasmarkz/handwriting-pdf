<#
.SYNOPSIS
  Install the handwriting-pdf skill for Codex and DeepSeek Harness (DSH).

.DESCRIPTION
  Creates a private Python venv under ~/.handwriting-pdf/venv, installs the runtime
  dependencies there, writes the bin/hwpdf launchers, and copies this skill into the
  user-level skill directories of both hosts.

.PARAMETER Target
  both (default) | dsh | codex | none (only prepare the venv + local launchers)

.PARAMETER VenvDir
  Override the venv location (default: %USERPROFILE%\.handwriting-pdf\venv)

.PARAMETER Python
  Python launcher to use (default: python; try py -3 or python3 if that fails)

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Target dsh
#>
[CmdletBinding()]
param(
    [ValidateSet('both', 'dsh', 'codex', 'none')]
    [string]$Target = 'both',
    [string]$VenvDir = (Join-Path $env:USERPROFILE '.handwriting-pdf\venv'),
    [string]$Python = 'python',
    [switch]$SkipDeps
)

$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "error: $msg" -ForegroundColor Red; exit 1 }

Write-Step "source: $src"

# --- 1. venv -------------------------------------------------------------
$pyExe = Join-Path $VenvDir 'Scripts\python.exe'
if (-not (Test-Path $pyExe)) {
    Write-Step "creating venv at $VenvDir"
    New-Item -ItemType Directory -Force (Split-Path -Parent $VenvDir) | Out-Null
    & $Python -m venv $VenvDir
    if (-not (Test-Path $pyExe)) { Fail "venv creation failed - install Python 3.10+ and retry with -Python <path>" }
}

if (-not $SkipDeps) {
    Write-Step "installing dependencies (first run downloads ~60 MB)"
    & $pyExe -m pip install --disable-pip-version-check -q --upgrade pip | Out-Null
    & $pyExe -m pip install --disable-pip-version-check -q -r (Join-Path $src 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed - check the network or run again with -SkipDeps" }
}

Write-Step "checking runtime"
& $pyExe (Join-Path $src 'scripts\hwpdf.py') doctor
if ($LASTEXITCODE -ne 0) { Fail "hwpdf doctor reported a problem" }

# --- 2. launchers --------------------------------------------------------
function Write-Launchers([string]$root) {
    $bin = Join-Path $root 'bin'
    New-Item -ItemType Directory -Force $bin | Out-Null
    $cli = Join-Path $root 'scripts\hwpdf.py'
    $cmd = @"
@echo off
"$pyExe" "$cli" %*
"@
    Set-Content -Path (Join-Path $bin 'hwpdf.cmd') -Value $cmd -Encoding ASCII
    $ps1 = @"
& "$pyExe" "$cli" @args
exit `$LASTEXITCODE
"@
    Set-Content -Path (Join-Path $bin 'hwpdf.ps1') -Value $ps1 -Encoding UTF8
    $sh = "#!/bin/sh`nexec `"$pyExe`" `"$cli`" `"`$@`"`n"
    $shPath = Join-Path $bin 'hwpdf'
    Set-Content -Path $shPath -Value $sh -Encoding UTF8 -NoNewline
    Write-Host "    launchers -> $bin"
}

Write-Launchers $src

# --- 3. install into host skill dirs -------------------------------------
function Install-Skill([string]$dest) {
    $resolved = $null
    if (Test-Path $dest) { $resolved = (Resolve-Path $dest).Path }
    $srcResolved = (Resolve-Path $src).Path
    if ($resolved -and ($resolved -eq $srcResolved)) {
        Write-Host "    skip (already in place): $dest"
        return
    }
    Write-Step "installing skill -> $dest"
    New-Item -ItemType Directory -Force $dest | Out-Null
    foreach ($f in @('SKILL.md', 'README.md', 'requirements.txt', 'install.ps1', 'install.sh')) {
        $from = Join-Path $src $f
        if (Test-Path $from) { Copy-Item $from (Join-Path $dest $f) -Force }
    }
    foreach ($d in @('scripts', 'references')) {
        $to = Join-Path $dest $d
        if (Test-Path $to) { Remove-Item -Recurse -Force $to }
        Copy-Item (Join-Path $src $d) $to -Recurse -Force
    }
    Get-ChildItem $dest -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Launchers $dest
}

$dshDest = Join-Path $env:USERPROFILE '.agents\skills\handwriting-pdf'
$codexDest = Join-Path $env:USERPROFILE '.codex\skills\handwriting-pdf'
switch ($Target) {
    'both' { Install-Skill $dshDest; Install-Skill $codexDest }
    'dsh' { Install-Skill $dshDest }
    'codex' { Install-Skill $codexDest }
    'none' { Write-Host '    skipped host installs (-Target none)' }
}

Write-Host ''
Write-Host 'Done.' -ForegroundColor Green
$launcher = Join-Path $src 'bin\hwpdf.cmd'
Write-Host "  try:  `"$launcher`" doctor"
Write-Host "  then: `"$launcher`" template --text-file letter.txt -d hw"
Write-Host '  restart the host app (or start a new session) so it picks up the new skill.'
