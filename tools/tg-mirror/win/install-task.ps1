<#
Registers the hourly task, with the three settings that make it run on a machine
nobody is sitting at.

Why PowerShell and not schtasks
-------------------------------
`schtasks /Create` cannot express any of what is actually wanted here: waking the
machine, starting late after a missed slot, or running on battery. Its only route
to them is an XML file, which has to be UTF-16 and is miserable to emit from a
.cmd. The ScheduledTasks cmdlets set all of it directly.

The three settings
------------------
LogonType Password   "Run whether user is logged on or not". The task then runs in
                     a background session with no desktop, which suits a script
                     that prints to a log. This is the one that needs a password:
                     Windows has to be able to log the account in unattended, so
                     the credential is stored in Credential Manager.

WakeToRun            "Wake the computer to run this task". Brings the machine out
                     of sleep or hibernation at the scheduled minute. It cannot
                     resurrect a machine that is shut down — nothing can — and it
                     is also subject to Power Options → Sleep → Allow wake timers,
                     which some laptops ship disabled. Checked at the end.

StartWhenAvailable   Runs as soon as possible after a missed slot, so hours spent
                     off or shut down are caught up rather than skipped.

Plus AllowStartIfOnBatteries and DontStopIfGoingOnBatteries, without which a
laptop on battery simply never runs the task, and Hidden so no console flashes.
#>

$ErrorActionPreference = 'Stop'
$TaskName = 'TG mirror'
$Runner = Join-Path $PSScriptRoot 'run-mirror.cmd'

if (-not (Test-Path $Runner)) { throw "run-mirror.cmd not found beside this script: $Runner" }

# Storing a credential for a task is an administrative act, so this needs
# elevation. Re-launching rather than failing: being told "run as administrator"
# after typing a password is a poor trade.
$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Needs administrator rights to store the task credential. Re-launching…'
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-NoExit', '-File', "`"$PSCommandPath`""
    )
    return
}

$user = "$env:USERDOMAIN\$env:USERNAME"
Write-Host ''
Write-Host "Task:     $TaskName"
Write-Host "Runs:     $Runner"
Write-Host "As:       $user  (whether logged on or not)"
Write-Host "Schedule: every hour, at 7 minutes past"
Write-Host ''
Write-Host 'Windows needs this account''s password to run the task while you are'
Write-Host 'logged out. It is stored by Windows in Credential Manager, not by this'
Write-Host 'script, and it is not written to disk or to the log.'
Write-Host ''
Write-Host 'If the account signs in with a PIN or Windows Hello only, use the'
Write-Host 'Microsoft account password. A blank password cannot work: Windows'
Write-Host 'refuses to run unattended tasks for an account that has none.'
Write-Host ''

$secure = Read-Host -Prompt "Password for $user" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
} finally {
    # Zeroed as soon as it has been handed over, so it does not sit in memory for
    # the life of the shell.
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}
if ([string]::IsNullOrEmpty($plain)) { throw 'No password given; nothing was registered.' }

$action = New-ScheduledTaskAction -Execute $Runner -WorkingDirectory (Split-Path $PSScriptRoot -Parent)

# One trigger that repeats forever, rather than a Daily/Hourly pair: -Once with a
# repetition interval is the shape that survives a reboot without drifting.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date -Hour 0 -Minute 7 -Second 0) `
    -RepetitionInterval (New-TimeSpan -Hours 1)

$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -Hidden `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -User $user -Password $plain -RunLevel Limited -Force | Out-Null

$plain = $null
Write-Host ''
Write-Host "Registered '$TaskName'." -ForegroundColor Green

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host ''
Write-Host 'Confirmed on the registered task:'
Write-Host ("  logon type        {0}   (Password = runs while logged out)" -f $task.Principal.LogonType)
Write-Host ("  wake to run       {0}" -f $task.Settings.WakeToRun)
Write-Host ("  catch up misses   {0}" -f $task.Settings.StartWhenAvailable)
Write-Host ("  runs on battery   {0}" -f (-not $task.Settings.DisallowStartIfOnBatteries))
Write-Host ("  hidden            {0}" -f $task.Settings.Hidden)
Write-Host ("  next run          {0}" -f $info.NextRunTime)

# A task can ask to wake the machine and be overruled by the power plan, silently.
# Reported rather than changed: altering someone's power settings without asking is
# not this script's business.
Write-Host ''
$wake = (powercfg /query SCHEME_CURRENT SUB_SLEEP RTCWAKE) 2>$null
if ($wake -and ($wake -join "`n") -match 'Current AC Power Setting Index:\s*0x00000000') {
    Write-Host 'Wake timers are DISABLED in the current power plan, which overrules' -ForegroundColor Yellow
    Write-Host 'WakeToRun: the task will not wake the machine. To allow it:' -ForegroundColor Yellow
    Write-Host '  Control Panel -> Power Options -> Change plan settings ->' -ForegroundColor Yellow
    Write-Host '  Change advanced power settings -> Sleep -> Allow wake timers -> Enable' -ForegroundColor Yellow
} else {
    Write-Host 'Wake timers appear to be allowed by the current power plan.'
}

Write-Host ''
Write-Host 'Check it end to end now:'
Write-Host "  schtasks /Run /TN `"$TaskName`""
Write-Host ("  type `"{0}\logs\mirror-*.log`"" -f (Split-Path $PSScriptRoot -Parent))
Write-Host ''
Write-Host 'Later:'
Write-Host "  Get-ScheduledTaskInfo -TaskName `"$TaskName`"     # last result and next run"
Write-Host "  Disable-ScheduledTask  -TaskName `"$TaskName`""
Write-Host "  Unregister-ScheduledTask -TaskName `"$TaskName`" -Confirm:`$false"
Write-Host ''
Write-Host 'Note: a task cannot run while the machine is fully shut down. WakeToRun'
Write-Host 'covers sleep and hibernation; after a shutdown, StartWhenAvailable makes'
Write-Host 'the first boot catch up the missed hours.'
