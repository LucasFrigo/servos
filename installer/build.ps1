param(
    [switch]$BundleOllama
)

$ErrorActionPreference = "Stop"
$Installer = $PSScriptRoot
$Root = Split-Path -Parent $Installer
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing $Python. Create the virtual environment and install requirements.txt first."
}

& $Python -m pip install "pyinstaller>=6.14"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Push-Location $Root
try {
    & $Python -m PyInstaller (Join-Path $Installer "Manivela.spec") --noconfirm --clean --distpath (Join-Path $Root "dist") --workpath (Join-Path $Root "build")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}

$models = Join-Path $Root "models"
$dest = Join-Path $Root "dist\Manivela\models"
if (Test-Path -LiteralPath $models) {
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Copy-Item -Path (Join-Path $models "*") -Destination $dest -Recurse -Force
    Write-Host "Copied speech models to $dest"
}
else {
    Write-Host "No models folder. The app will download voices and speech models on first launch."
}

if ($BundleOllama) {
    $payload = Join-Path $Installer "payload"
    New-Item -ItemType Directory -Force -Path $payload | Out-Null
    $setup = Join-Path $payload "OllamaSetup.exe"
    if (-not (Test-Path -LiteralPath $setup)) {
        Write-Host "Downloading OllamaSetup.exe into the installer payload..."
        & curl.exe -L --fail -o $setup "https://ollama.com/download/OllamaSetup.exe"
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}

$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if (-not $iscc) {
    Write-Host "Manivela.exe is in dist\Manivela."
    Write-Host "Install Inno Setup 6, then run this script again to compile installer\Output\Manivela-Setup.exe."
    exit 0
}

& $iscc (Join-Path $Installer "Manivela.iss")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Installer: $(Join-Path $Installer 'Output\Manivela-Setup.exe')"
