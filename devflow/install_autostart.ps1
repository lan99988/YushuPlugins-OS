[CmdletBinding()]
param(
    [ValidateSet('Plan','Install','Launch','Uninstall')][string]$Mode = 'Plan',
    [string]$Repo, [string]$Python, [string]$Manifest, [string]$Codex, [string]$Gh,
    [Parameter(Mandatory=$true)][string]$State,
    [switch]$StartNow
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Absolute-Existing([string]$Value, [string]$Label) {
    if (-not $Value -or -not [IO.Path]::IsPathRooted($Value)) { throw "$Label must be an absolute path" }
    return (Resolve-Path -LiteralPath $Value -ErrorAction Stop).Path
}
function Native-Quote([string]$Value) {
    # Windows CommandLineToArgvW quoting, including trailing backslashes.
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}
if (-not [IO.Path]::IsPathRooted($State)) { throw 'State must be an absolute path' }
$State = [IO.Path]::GetFullPath($State)
if ($Mode -eq 'Launch') {
    $configuration = Get-Content -LiteralPath (Join-Path $State 'scheduler.json') -Raw | ConvertFrom-Json
    $Repo = Absolute-Existing $configuration.Repo 'Repo'
    $Python = Absolute-Existing $configuration.Python 'Python'
    $Codex = Absolute-Existing $configuration.Codex 'Codex'
    $Gh = Absolute-Existing $configuration.Gh 'Gh'
    Remove-Item Env:GH_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
    Set-Location -LiteralPath $Repo
    $taskName = $configuration.TaskName
    $mutex = [Threading.Mutex]::new($false, ('Global\' + $taskName))
    $owns = $false
    try {
        try { $owns = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owns = $true }
        if (-not $owns) { exit 0 }
        $env:DEVFLOW_CODEX = $Codex
        $env:DEVFLOW_GH = $Gh
        $arguments = @('-m','devflow','--repo',$Repo,'--state',$State,'run','--watch','--workers','3','--publish','auto')
        $nativeArguments = ($arguments | ForEach-Object { Native-Quote $_ }) -join ' '
        while ($true) {
            $statusArguments = @('-m','devflow','--repo',$Repo,'--state',$State,'status')
            $status = (& $Python @statusArguments | Out-String | ConvertFrom-Json)
            if ($status.control -in @('stopped','blocked_capability','blocked_spec')) { exit 0 }
            $child = Start-Process -FilePath $Python -ArgumentList $nativeArguments -WorkingDirectory $Repo -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $State 'daemon.stdout.log') -RedirectStandardError (Join-Path $State 'daemon.stderr.log')
            $child.WaitForExit()
            if ($child.ExitCode -eq 0) { exit 0 }
            Start-Sleep -Seconds 30
        }
    } finally {
        if ($owns) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
    exit 0
}
$Repo = Absolute-Existing $Repo 'Repo'
if ($State.Equals($Repo, [StringComparison]::OrdinalIgnoreCase) -or $State.StartsWith($Repo.TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'State must be outside repository' }
$sha = [Security.Cryptography.SHA256]::Create()
try { $digest = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Repo.ToLowerInvariant())))).Replace('-','').Substring(0,16).ToLowerInvariant() } finally { $sha.Dispose() }
$taskName = 'YushuOS-Devflow-' + $digest
if ($Mode -eq 'Uninstall') { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false; exit 0 }
$Python = Absolute-Existing $Python 'Python'
$Manifest = Absolute-Existing $Manifest 'Manifest'
$Codex = Absolute-Existing $Codex 'Codex'
$Gh = Absolute-Existing $Gh 'Gh'
$scriptPath = Absolute-Existing $PSCommandPath 'Script'
$powershellPath = Absolute-Existing (Join-Path $PSHOME 'powershell.exe') 'PowerShell'
$arguments = @('-NoLogo','-NoProfile','-NonInteractive','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',$scriptPath,'-Mode','Launch','-State',$State)
$nativeArguments = ($arguments | ForEach-Object { Native-Quote $_ }) -join ' '
$configuration = [ordered]@{Repo=$Repo;Python=$Python;Manifest=$Manifest;Codex=$Codex;Gh=$Gh;State=$State;TaskName=$taskName;Execute=$powershellPath;Arguments=$nativeArguments;MultipleInstances='IgnoreNew'}
if ($Mode -eq 'Plan') { $configuration | ConvertTo-Json -Depth 5; exit 0 }
New-Item -ItemType Directory -Force -Path $State | Out-Null
$configuration | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $State 'scheduler.json') -Encoding UTF8
$action = New-ScheduledTaskAction -Execute $powershellPath -Argument $nativeArguments -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
if ($StartNow) { Start-ScheduledTask -TaskName $taskName }
$configuration | ConvertTo-Json -Depth 5
