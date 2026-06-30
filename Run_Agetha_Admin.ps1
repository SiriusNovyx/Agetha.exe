#Requires -Version 5.1
<#
.SYNOPSIS
  Launch Agetha Medic_Checker with Administrator privileges.

.DESCRIPTION
  Elevated mode is required to control some protected windows (Task Manager,
  elevated terminals, etc.). Only use when you trust Agetha's command permissions.
#>

$root = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
$medic = Join-Path $root 'Medic_Checker.ps1'

if (-not (Test-Path -LiteralPath $medic)) {
    Write-Error "Medic_Checker.ps1 not found in $root"
    exit 1
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

if ($isAdmin) {
    & $medic
    exit $LASTEXITCODE
}

$msg = @"
Agetha will run as Administrator.

This allows window move/resize/close on elevated apps.
Dangerous commands still require your confirmation.

Continue?
"@

$ws = New-Object -ComObject WScript.Shell
$btn = $ws.Popup($msg, 0, 'Agetha - Elevated Launch', 4 + 48)
if ($btn -ne 6) {
    exit 0
}

Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$medic`""
)
