param(
    [string]$TaskName = "Sports News Aggregator - CFB Refresh",
    [string[]]$Times = @("06:00", "12:00", "18:00", "23:00"),
    [int]$Season = 0
)

$runner = Join-Path $PSScriptRoot "run_scheduled_refresh.ps1"
$powershell = Join-Path $PSHOME "powershell.exe"
$seasonArgument = if ($Season -gt 0) { " -Season $Season" } else { "" }
$actionArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$runner`"$seasonArgument"
$action = New-ScheduledTaskAction -Execute $powershell -Argument $actionArguments
$triggers = foreach ($time in $Times) {
    New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($time, "HH:mm", $null))
}
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal `
    -Description "Refresh college-football data, reporting, clusters, and scores." -Force | Out-Null
Write-Output "Registered '$TaskName' for $($Times -join ', ') local time."
