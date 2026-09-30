$ErrorActionPreference = "Stop"
$Model = "qwen2.5:7b"

function Find-OllamaExe {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"),
        (Join-Path $env:ProgramFiles "Ollama\ollama.exe")
    )
    foreach ($path in $candidates) {
        if (Test-Path -LiteralPath $path) {
            return $path
        }
    }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $env:Path = "$userPath;$machinePath;$env:Path"
    $cmd = Get-Command ollama.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    return $null
}

$ollama = Find-OllamaExe
if (-not $ollama) {
    $setup = Join-Path $PSScriptRoot "OllamaSetup.exe"
    if (-not (Test-Path -LiteralPath $setup)) {
        $setup = Join-Path $env:TEMP "OllamaSetup.exe"
        Write-Host "Downloading Ollama..."
        & curl.exe -L --fail -o $setup "https://ollama.com/download/OllamaSetup.exe"
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    Write-Host "Installing Ollama..."
    $proc = Start-Process -FilePath $setup -ArgumentList "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES" -Wait -PassThru
    if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 3010) {
        exit $proc.ExitCode
    }
    $ollama = Find-OllamaExe
}

if (-not $ollama) {
    Write-Host "Ollama was not installed."
    exit 1
}

Write-Host "Downloading language model $Model"
& $ollama pull $Model
exit $LASTEXITCODE
