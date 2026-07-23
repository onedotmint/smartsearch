# Rebuild the Python venv inside the mise install dir for
# @onedotmint/smart-search using a clean (non-mise-shim) python interpreter.
#
# Why: mise's npm backend may skip the postinstall venv setup, or run it with
# a python from PATH that resolves to a mise shim — copying a launcher into
# the venv that fails inside Codex's sandboxed user identity. This script
# always uses an absolute-path real python to ensure the venv contains a
# clean python.exe and the smart-search package is installed properly.
#
# Run after every `mise install` or `mise upgrade npm:@onedotmint/smart-search`.
#
# Override the python source by setting $env:SMART_SEARCH_REBUILD_PYTHON,
# otherwise default to scoop's python313.

$ErrorActionPreference = 'Stop'

$pkgRoot = Join-Path $env:LOCALAPPDATA 'mise\installs\npm-onedotmint-smart-search'
if (-not (Test-Path -LiteralPath $pkgRoot)) {
    Write-Error "smart-search mise install dir not found: $pkgRoot. Run 'mise install npm:@onedotmint/smart-search@latest' first."
}

$miseInstallDir = Get-ChildItem -LiteralPath $pkgRoot -Directory -ErrorAction SilentlyContinue |
    Sort-Object -Property @{ Expression = { try { [version]$_.Name } catch { [version]'0.0.0' } } } -Descending |
    Select-Object -First 1
if (-not $miseInstallDir) {
    Write-Error "no installed version dir under $pkgRoot."
}

# npm extracts the package to node_modules/@onedotmint/smart-search/, which is
# what npm/bin/smart-search.js treats as packageRoot (and where venvDir lives).
$packageDir = Join-Path $miseInstallDir.FullName 'node_modules\@onedotmint\smart-search'
if (-not (Test-Path -LiteralPath (Join-Path $packageDir 'pyproject.toml'))) {
    Write-Error "pyproject.toml not found under $packageDir. mise install may have failed."
}

$python = if ($env:SMART_SEARCH_REBUILD_PYTHON) { $env:SMART_SEARCH_REBUILD_PYTHON }
          else { 'D:\scoop\apps\python313\current\python.exe' }
if (-not (Test-Path -LiteralPath $python)) {
    Write-Error "python interpreter not found: $python. Set SMART_SEARCH_REBUILD_PYTHON to override."
}

$venvDir = Join-Path $packageDir '.smart-search-python'
Write-Host "package dir    : $packageDir"
Write-Host "rebuild target : $venvDir"
Write-Host "python source  : $python"

if (Test-Path -LiteralPath $venvDir) {
    Write-Host "removing existing venv..."
    Remove-Item -LiteralPath $venvDir -Recurse -Force
}

Write-Host "creating venv..."
& $python -m venv $venvDir
if ($LASTEXITCODE -ne 0) { Write-Error "venv creation failed (exit $LASTEXITCODE)" }

$venvPython = Join-Path $venvDir 'Scripts\python.exe'
Write-Host "installing smart-search into venv..."
& $venvPython -m pip install --disable-pip-version-check $packageDir
if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed (exit $LASTEXITCODE)" }

Write-Host ""
Write-Host "done. venv ready at: $venvDir"
& $venvPython -c "import smart_search; print('smart_search module:', smart_search.__file__)"
