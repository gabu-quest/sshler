param(
    [string]$Host = "127.0.0.1",
    [int]$Port = 8822
)

$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path -Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

$logDir = Join-Path $repoRoot 'logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stdoutLog = Join-Path $logDir 'sshler.stdout.log'
$stderrLog = Join-Path $logDir 'sshler.stderr.log'

$arguments = @('run', 'sshler', 'serve', '--host', $Host, '--port', $Port)

Start-Process -FilePath 'uv' `
    -ArgumentList $arguments `
    -WorkingDirectory $repoRoot `
    -NoNewWindow `
    -Wait `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog
