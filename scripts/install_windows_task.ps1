param(
    [string]$TaskName = "VLESS Cloudflare Speed DNS",
    [string]$At = "03:00"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$RunScript = Join-Path $ScriptDir "run_once.ps1"

if (-not (Test-Path $RunScript)) {
    throw "Run script not found: $RunScript"
}

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`"" `
    -WorkingDirectory $ProjectDir

$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Run CloudflareSpeedTest daily and update VLESS DNS acceleration records." `
    -Force | Out-Null

Write-Host "Scheduled task installed: $TaskName"
Write-Host "Project: $ProjectDir"
Write-Host "Daily at: $At"
Write-Host "Run now: Start-ScheduledTask -TaskName `"$TaskName`""
