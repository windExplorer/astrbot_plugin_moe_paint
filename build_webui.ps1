# Build the gallery WebUI (webui-src -> pages/moe-paint).
# Called by build_zip.ps1 automatically; can also be run standalone.
# Keep ASCII-only: PS 5.1 on a non-UTF8 code page can mis-parse CJK comments.

$ErrorActionPreference = "Stop"

if ($PSScriptRoot) {
    $root = $PSScriptRoot
} else {
    $root = (Get-Location).Path
}

$srcDir = Join-Path $root "webui-src"
if (-not (Test-Path (Join-Path $srcDir "package.json"))) {
    Write-Host "ERROR: webui-src/package.json not found" -ForegroundColor Red
    exit 1
}

Push-Location $srcDir
try {
    if (-not (Test-Path (Join-Path $srcDir "node_modules"))) {
        Write-Host "node_modules missing, running npm install ..."
        npm install
        if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
    }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
} finally {
    Pop-Location
}

$pageIndex = Join-Path $root "pages/moe-paint/index.html"
if (-not (Test-Path $pageIndex)) {
    Write-Host "ERROR: build finished but pages/moe-paint/index.html missing" -ForegroundColor Red
    exit 1
}
Write-Host "WebUI built: pages/moe-paint/index.html" -ForegroundColor Green
