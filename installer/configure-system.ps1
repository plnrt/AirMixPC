[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Install','Uninstall')][string]$Mode,
    [Parameter(Mandatory)][string]$InstallDir,
    [Parameter(Mandatory)][string]$DataDir
)
$ErrorActionPreference = 'Stop'
$firewallGroup = 'AirMix PC'
$logDir = Join-Path $DataDir 'Logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'installer.log'
Start-Transcript -Path $log -Append | Out-Null
try {
    Get-NetFirewallRule -Group $firewallGroup -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    if ($Mode -eq 'Uninstall') { return }

    $exe = Join-Path $InstallDir 'AirMixPC.exe'
    if (-not (Test-Path -LiteralPath $exe)) { throw "Missing installed executable: $exe" }

    # Persist exact known home profiles as Private without switching networks.
    $profilesKey = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\NetworkList\Profiles'
    if (Test-Path $profilesKey) {
        Get-ChildItem $profilesKey | ForEach-Object {
            $profile = Get-ItemProperty $_.PSPath
            if ($profile.ProfileName -in @('admin_new','admin_new_5g')) {
                Set-ItemProperty $_.PSPath -Name Category -Type DWord -Value 1
            }
        }
    }

    New-NetFirewallRule -DisplayName 'AirMix PC TCP (Private)' -Group $firewallGroup `
        -Direction Inbound -Action Allow -Profile Private -Protocol TCP -Program $exe | Out-Null
    New-NetFirewallRule -DisplayName 'AirMix PC UDP (Private)' -Group $firewallGroup `
        -Direction Inbound -Action Allow -Profile Private -Protocol UDP -Program $exe | Out-Null

    # Pairing keys and the trusted-device register are private to this user.
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls.exe $DataDir /inheritance:r /grant:r "${identity}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
}
finally {
    Stop-Transcript | Out-Null
}
