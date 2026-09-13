[CmdletBinding()]
param(
    [ValidateSet('Install','Repair')][string]$Action = 'Install'
)
$ErrorActionPreference = 'Stop'
$appName = 'AirMix PC'
$installDir = Join-Path $env:ProgramFiles 'AirMixPC'
$sourceDir = $PSScriptRoot
$exe = Join-Path $installDir 'AirMixPC.exe'
$firewallGroup = 'AirMix PC'

function Invoke-Elevated {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        $args = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"{0}"' -f $PSCommandPath),'-Action',$Action)
        $process = Start-Process powershell.exe -Verb RunAs -ArgumentList $args -Wait -PassThru
        exit $process.ExitCode
    }
}

function New-Link([string]$Path, [string]$Target, [string]$WorkingDirectory) {
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath = $Target
    $link.WorkingDirectory = $WorkingDirectory
    $link.Description = $appName
    $link.Save()
}

Invoke-Elevated
if (-not (Test-Path (Join-Path $sourceDir 'uxplay.exe'))) { throw 'Run setup from the extracted AirMixPC package.' }
Get-Process AirMixPC,uxplay -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
if ([IO.Path]::GetFullPath($sourceDir).TrimEnd('\') -ne [IO.Path]::GetFullPath($installDir).TrimEnd('\')) {
    Get-ChildItem -LiteralPath $sourceDir -Force | Copy-Item -Destination $installDir -Recurse -Force
}

# Persist both known home profiles as Private without changing the active SSID.
$profilesKey = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\NetworkList\Profiles'
if (Test-Path $profilesKey) {
    Get-ChildItem $profilesKey | ForEach-Object {
        $profile = Get-ItemProperty $_.PSPath
        if ($profile.ProfileName -in @('admin_new','admin_new_5g')) {
            Set-ItemProperty $_.PSPath -Name Category -Type DWord -Value 1
        }
    }
}

Get-NetFirewallRule -Group $firewallGroup -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'AirMix PC TCP (Private)' -Group $firewallGroup -Direction Inbound `
    -Action Allow -Profile Private -Protocol TCP -Program $exe | Out-Null
New-NetFirewallRule -DisplayName 'AirMix PC UDP (Private)' -Group $firewallGroup -Direction Inbound `
    -Action Allow -Profile Private -Protocol UDP -Program $exe | Out-Null

$programs = [Environment]::GetFolderPath('Programs')
$startup = [Environment]::GetFolderPath('Startup')
New-Link (Join-Path $programs 'AirMix PC\AirMix PC.lnk') $exe $installDir
New-Link (Join-Path $startup 'AirMix PC.lnk') $exe $installDir

$uninstallKey = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\AirMixPC'
New-Item -Force $uninstallKey | Out-Null
Set-ItemProperty $uninstallKey DisplayName $appName
Set-ItemProperty $uninstallKey DisplayVersion '1.0.0'
Set-ItemProperty $uninstallKey Publisher 'AirMix PC contributors'
Set-ItemProperty $uninstallKey InstallLocation $installDir
Set-ItemProperty $uninstallKey UninstallString ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f (Join-Path $installDir 'AirMixPC-Uninstall.ps1'))
Set-ItemProperty $uninstallKey ModifyPath ('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}" -Action Repair' -f (Join-Path $installDir 'AirMixPC-Setup.ps1'))

$dataDir = Join-Path $env:LOCALAPPDATA 'AirMixPC'
New-Item -ItemType Directory -Force $dataDir | Out-Null
& icacls.exe $dataDir /inheritance:r /grant:r "${env:USERNAME}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
Start-Process $exe -WorkingDirectory $installDir
Write-Host "$appName $Action complete."
