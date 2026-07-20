param(
    [string]$TaskName = 'sshler',
    [string]$Host = '127.0.0.1',
    [int]$Port = 8822,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

Import-Module ScheduledTasks

$repoRoot = Resolve-Path -Path (Join-Path $PSScriptRoot '..')
$runScript = Resolve-Path -Path (Join-Path $PSScriptRoot 'run-sshler.ps1')

$argumentLine = "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`" -Host $Host -Port $Port"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argumentLine -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal

Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force:$Force.IsPresent | Out-Null

Write-Host "Registered scheduled task '$TaskName'.":
Write-Host "  - Host : $Host"
Write-Host "  - Port : $Port"
Write-Host "Logs will stream to '$(Join-Path $repoRoot "logs")'."
