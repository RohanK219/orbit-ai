# Build orbit-ai into a standalone Windows .exe.
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
#
# Produces  dist\orbit-ai.exe  which runs on any Windows 10/11 machine with no
# Python installed. Copy that single file to the target machine and run it.
#
# Safe to re-run; it cleans previous build output first.

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$venvPy = Join-Path $root '.venv\Scripts\python.exe'
$spec = Join-Path $root 'packaging\orbit-ai.spec'
$validation = Join-Path $root 'scripts\validate_packaging.py'
$work = Join-Path $root 'build'
$dist = Join-Path $root 'dist'

if (-not (Test-Path $venvPy)) {
    Write-Error "Virtual environment not found at $venvPy. Create it and install requirements first."
    exit 1
}
if (-not (Test-Path $spec)) {
    Write-Error "PyInstaller spec not found at $spec"
    exit 1
}
if (-not (Test-Path $validation)) {
    Write-Error "Packaging validation script not found at $validation"
    exit 1
}

Write-Host "==> Ensuring PyInstaller is installed"
& $venvPy -m pip install "pyinstaller>=6.6,<7" --disable-pip-version-check --quiet 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "Could not install PyInstaller (pip exit code $LASTEXITCODE). Check the pip output above."
    exit $LASTEXITCODE
}

Write-Host "==> Validating packaging inputs"
& $venvPy $validation
if ($LASTEXITCODE -ne 0) {
    Write-Error "Packaging validation failed; build was not started."
    exit $LASTEXITCODE
}

Write-Host "==> Cleaning previous build output"
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
if (Test-Path $dist) { Remove-Item -Recurse -Force $dist }

Write-Host "==> Building (this takes a minute or two)"
# Run from the packaging dir so the spec's relative pathex (../src) resolves.
Push-Location (Join-Path $root 'packaging')
try {
    & $venvPy -m PyInstaller $spec --noconfirm --clean --log-level WARN --distpath $dist --workpath $work
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($code -ne 0) {
    Write-Error "PyInstaller failed with exit code $code"
    exit $code
}

$exe = Join-Path $dist 'orbit-ai.exe'
if ((Test-Path $exe) -and ((Get-Item $exe).Length -gt 0)) {
    $sizeMB = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host ""
    Write-Host "==> Built  $exe  ($sizeMB MB)" -ForegroundColor Green
    Write-Host "    Copy this single file to the target machine and run it."
} else {
    Write-Error "Build reported success but $exe is missing or empty. Check PyInstaller warnings above."
    exit 1
}
