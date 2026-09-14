[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$compiler = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $compiler) { throw 'Inno Setup 6 is required (winget install JRSoftware.InnoSetup).' }
if (-not (Test-Path (Join-Path $root 'dist\AirMixPC\AirMixPC.exe'))) {
    throw 'Build dist\AirMixPC first with build.sh.'
}
& $compiler (Join-Path $PSScriptRoot 'AirMixPC.iss')
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compiler failed with exit code $LASTEXITCODE" }
$output = Join-Path $root 'dist\installer\AirMixPC-Setup-1.0.2.exe'
$hash = (Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path (Split-Path $output) 'SHA256SUMS.txt') -Encoding ASCII `
    -Value "$hash  AirMixPC-Setup-1.0.2.exe"
