param(
    [string]$TaskName = 'sshler'
)

$ErrorActionPreference = 'Stop'

Import-Module ScheduledTasks

if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
    Write-Host "Scheduled task '$TaskName' was not found."
    return
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed scheduled task '$TaskName'."
