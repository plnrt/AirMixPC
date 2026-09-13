[CmdletBinding()]
param([switch]$PurgeUserData)
$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    $args = @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"{0}"' -f $PSCommandPath))
    if ($PurgeUserData) { $args += '-PurgeUserData' }
    $process = Start-Process powershell.exe -Verb RunAs -ArgumentList $args -Wait -PassThru
    exit $process.ExitCode
}
$installDir = Join-Path $env:ProgramFiles 'AirMixPC'
Get-Process AirMixPC,uxplay -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-NetFirewallRule -Group 'AirMix PC' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('Startup')) 'AirMix PC.lnk') -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('Programs')) 'AirMix PC') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\AirMixPC' -Recurse -Force -ErrorAction SilentlyContinue
if ($PurgeUserData) {
    Remove-Item -LiteralPath (Join-Path $env:LOCALAPPDATA 'AirMixPC') -Recurse -Force -ErrorAction SilentlyContinue
}
# Deliberately leaves Bluetooth Audio Receiver, drivers and network profile categories untouched.
$cleanup = Join-Path $env:TEMP ('AirMixPC-uninstall-' + [guid]::NewGuid().ToString('N') + '.cmd')
Set-Content -LiteralPath $cleanup -Encoding ASCII -Value "@echo off`r`ntimeout /t 2 /nobreak >nul`r`nrmdir /s /q `"$installDir`"`r`ndel /q `"%~f0`""
Start-Process cmd.exe -WindowStyle Hidden -ArgumentList @('/c',('"{0}"' -f $cleanup))
