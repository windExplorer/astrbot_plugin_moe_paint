# Package astrbot_plugin_moe_paint into an AstrBot-installable zip.
#
# Design notes (learned from sibling plugins, keep them):
#   1) EXPLICIT include list. New top-level .py modules MUST be added here too,
#      otherwise the repo has the file but the released zip silently misses it.
#      A guard below fails the build loudly if any top-level .py is missing here.
#   2) Refuse to overwrite an existing zip of the same version: bump the version
#      in metadata.yaml (and add a CHANGELOG entry) before repacking. Historical
#      zips in dist/ are kept forever so users can roll back.
#   3) Archive layout: wrapped in a top-level folder named after the plugin, with
#      explicit directory entries and forward slashes. Do NOT use PowerShell
#      Compress-Archive (it omits directory entries).
#   4) dist/ is git-ignored: the zip is a local install artifact, not source.
#   5) tar is resolved from System32 explicitly: in a Git Bash environment the
#      MSYS tar on PATH treats "X:" as a host name and fails on zips.
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File .\build_zip.ps1
#
# NOTE: keep this file ASCII-only. PS 5.1 on a non-UTF8 code page can mis-parse
#       UTF-8 no-BOM files that contain CJK comments.

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

if ($PSScriptRoot) {
    $root = $PSScriptRoot
} elseif ($MyInvocation.MyCommand.Path) {
    $root = Split-Path -Parent $MyInvocation.MyCommand.Path
} else {
    $root = (Get-Location).Path
}

$pluginName = "astrbot_plugin_moe_paint"
$distDir = Join-Path $root "dist"

# --- version from metadata.yaml (UTF8) ---
$metaPath = Join-Path $root "metadata.yaml"
if (-not (Test-Path $metaPath)) {
    Write-Host "ERROR: metadata.yaml not found" -ForegroundColor Red
    exit 1
}
$metaRaw = Get-Content -Raw -Encoding UTF8 $metaPath
$version = ""
if ($metaRaw -match '(?m)^\s*version:\s*v?([0-9][^\s#]*)') {
    $version = $Matches[1].Trim()
}
if (-not $version) {
    Write-Host "ERROR: cannot read version from metadata.yaml" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $distDir)) {
    New-Item -ItemType Directory -Path $distDir | Out-Null
}
$zipName = "${pluginName}_v${version}.zip"
$zipPath = Join-Path $distDir $zipName

if (Test-Path $zipPath) {
    Write-Host "ERROR: $zipName already exists. Bump 'version' in metadata.yaml and add a CHANGELOG entry before repacking." -ForegroundColor Red
    exit 1
}

# --- build the WebUI first (pages/moe-paint is built at pack time, not committed) ---
& (Join-Path $root "build_webui.ps1")
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: build_webui.ps1 failed" -ForegroundColor Red
    exit 1
}

# --- files / directories that go into the release ---
# Keep this in sync with main.py's import list and the modules under this folder.
$includeList = @(
    "main.py",
    "config_coerce.py",  # config read/write pure helpers (dot-path keys; unit-tested)
    "comfy_client.py",   # ComfyUI HTTP client (adapted from anima, AGPL same-source)
    "prompter.py",       # diversity draw + LLM JSON contract
    "workflow_mgr.py",   # workflow templates: load/locate/inject/rotate
    "scheduler.py",      # schedule loop (pure judges + thin shell)
    "gallery_store.py",  # sqlite records + sha256 archive + LRU
    "delivery.py",       # StarTools.send_message delivery
    "webui_api.py",      # gallery endpoints (official envelope + file/thumb)
    "_conf_schema.json",
    "metadata.yaml",
    "requirements.txt",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "builtin",           # builtin workflow JSON (copied to data dir on first run)
    "pages"              # built WebUI (produced by build_webui.ps1 just above)
)

# --- self-check: every top-level .py in the repo MUST be listed above ---
$topPy = @(Get-ChildItem $root -Filter *.py -File | Select-Object -ExpandProperty Name)
$notListed = @($topPy | Where-Object { $includeList -notcontains $_ })
if ($notListed.Count -gt 0) {
    Write-Host ("ERROR: top-level module(s) missing from includeList: " + ($notListed -join ", ")) -ForegroundColor Red
    Write-Host "       Add them to the list at the top of this script before packaging." -ForegroundColor Red
    exit 1
}
Write-Host ("includeList check OK: all " + $topPy.Count + " top-level .py files are listed")

function Add-ItemToZip($zip, $fsPath, $entryPath) {
    if (Test-Path -PathType Container $fsPath) {
        # directory entry must end with '/'
        $null = $zip.CreateEntry($entryPath + "/")
        Get-ChildItem $fsPath |
            Where-Object { $_.Name -ne "__pycache__" -and $_.Extension -notin @(".pyc", ".pyo") } |
            ForEach-Object {
                Add-ItemToZip $zip $_.FullName ($entryPath + "/" + $_.Name)
            }
    } else {
        # entries are named with forward slashes so Linux-side zipfile sees a real tree
        $entry = $zip.CreateEntry($entryPath, [System.IO.Compression.CompressionLevel]::Optimal)
        $stream = $entry.Open()
        try {
            $bytes = [System.IO.File]::ReadAllBytes($fsPath)
            $stream.Write($bytes, 0, $bytes.Length)
        } finally {
            $stream.Dispose()
        }
    }
}

$fs = [System.IO.File]::Open($zipPath, [System.IO.FileMode]::Create)
$zip = New-Object System.IO.Compression.ZipArchive($fs, [System.IO.Compression.ZipArchiveMode]::Create, $false)
try {
    foreach ($name in $includeList) {
        $fsPath = Join-Path $root $name
        if (-not (Test-Path $fsPath)) {
            Write-Host "ERROR: missing required item: $name" -ForegroundColor Red
            $zip.Dispose(); $fs.Dispose()
            Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
            exit 1
        }
        Add-ItemToZip $zip $fsPath ($pluginName + "/" + $name)
    }
} finally {
    $zip.Dispose()
    $fs.Dispose()
}

# --- verify the archive layout with System32 tar (see note 5) ---
$tarExe = Join-Path $env:SystemRoot "System32\tar.exe"
if (-not (Test-Path $tarExe)) { $tarExe = "tar" }
$entries = @(& $tarExe -tf $zipPath)
$sizeKb = [math]::Round((Get-Item $zipPath).Length / 1024, 1)
Write-Host "Packaged: $zipPath" -ForegroundColor Green
Write-Host ("Entries : {0}   Size: {1} KB" -f $entries.Count, $sizeKb)

# top level must be exactly the plugin folder (single wrapped dir)
$top = @($entries | ForEach-Object { ($_ -split '/')[0] } | Sort-Object -Unique)
Write-Host ("Top-level: " + ($top -join ","))
if ($top.Count -eq 1 -and $top[0] -eq $pluginName) {
    Write-Host "OK: wrapped layout as expected." -ForegroundColor Green
} else {
    Write-Host "WARN: unexpected archive layout, check packaging!" -ForegroundColor Yellow
}

# spot checks: metadata at plugin root, built page, builtin workflow
$mustHave = @(
    "$pluginName/metadata.yaml",
    "$pluginName/pages/moe-paint/index.html",
    "$pluginName/builtin/anima_t2i.json"
)
foreach ($m in $mustHave) {
    if ($entries -notcontains $m) {
        Write-Host "ERROR: zip missing expected entry: $m" -ForegroundColor Red
        exit 1
    }
}
Write-Host "OK: metadata / page / builtin workflow present." -ForegroundColor Green
