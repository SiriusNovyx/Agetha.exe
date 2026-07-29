#Requires -Version 5.1
<#
.SYNOPSIS
  Agetha startup health check and launcher (Overhaul Edition v5.5.1)

.DESCRIPTION
  Verifies project files, ARM64/x64 Python compatibility, venv, packages,
  optional Tesseract, assets (including presence loaf/sleep GIFs), config,
  realism/presence APIs, safety toggles, and py_compile - then launches main.py.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

$Script:Root = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
Set-Location -LiteralPath $Script:Root
# Initialized before Invoke-StandardChecks (StrictMode forbids reading unset script vars).
$script:VenvPython = $null
$script:PythonVersionInfo = $null
$script:PythonCmd = $null
$script:PreferredPythonExe = $null
$script:RequireX64Python = $false

function Get-ConfigValue {
    param(
        [Parameter(Mandatory)][string]$Key,
        [string]$Default = ''
    )
    $path = Join-Path $Script:Root 'config.txt'
    if (-not (Test-Path -LiteralPath $path)) { return $Default }
    $pattern = "^\s*$([regex]::Escape($Key))\s*=\s*(.*)\s*$"
    foreach ($line in Get-Content -LiteralPath $path) {
        if ($line -match '^\s*#') { continue }
        if ($line -match $pattern) {
            return $Matches[1].Trim()
        }
    }
    return $Default
}

function Get-AppVersion {
    $v = Get-ConfigValue -Key 'APP_VERSION' -Default '5.5.1'
    if ($v) { return $v }
    return '5.5.1'
}

function Write-Line([string]$Text, [ConsoleColor]$Color = 'Gray') {
    Write-Host $Text -ForegroundColor $Color
}

function Write-Ok([string]$Text)   { Write-Line "  [ OK ]  $Text" 'Green' }
function Write-Warn([string]$Text) { Write-Line "  [WARN]  $Text" 'Yellow' }
function Write-Fail([string]$Text) { Write-Line "  [FAIL]  $Text" 'Red' }
function Write-Info([string]$Text) { Write-Line "  [NOTICE]  $Text" 'DarkGray' }
function Write-Step([string]$Text) { Write-Line "  $Text" 'Cyan' }
function Write-Head([string]$Text) { Write-Line $Text 'White' }

try {
    $script:AppVersion = Get-AppVersion
    $Host.UI.RawUI.WindowTitle = "Agetha.exe  -  Health Check  |  v$script:AppVersion"
} catch {
    $script:AppVersion = '5.5.1'
}

function Test-GitHubUpdate {
    $check = Get-ConfigValue -Key 'CHECK_FOR_UPDATES' -Default 'yes'
    if ($check -notmatch '^(?i)yes$') { return }
    $url = Get-ConfigValue -Key 'GITHUB_RELEASES_URL' -Default ''
    if (-not $url) { return }
    try {
        $resp = Invoke-RestMethod -Uri $url -TimeoutSec 8 -Headers @{ 'User-Agent' = 'Agetha-Medic-Checker' }
        $remote = ($resp.tag_name -replace '^v', '').Trim()
        $local = Get-AppVersion
        if ($remote -and $remote -ne $local) {
            Write-Warn "Update available: v$remote (you have v$local)"
            if ($resp.html_url) { Write-Info "Release: $($resp.html_url)" }
        } else {
            Write-Ok "Version v$local is current."
        }
    } catch {
        Write-Info 'Update check skipped (no network or GITHUB_RELEASES_URL not set).'
    }
}

function New-AgethaDesktopShortcut {
    $create = Get-ConfigValue -Key 'CREATE_DESKTOP_SHORTCUT' -Default 'no'
    if ($create -notmatch '^(?i)yes$') { return }
    try {
        # Prefer venv Python (may run before Invoke-StandardChecks sets $script:VenvPython).
        $venvPy = Join-Path $Script:Root '.venv\Scripts\python.exe'
        if ($script:VenvPython) {
            $pyToast = $script:VenvPython
        } elseif (Test-Path -LiteralPath $venvPy) {
            $pyToast = $venvPy
        } else {
            $pyToast = 'python'
        }
        & $pyToast (Join-Path $Script:Root 'medic_helper.py') toast_shortcut 2>$null | Out-Null
        $desktop = [Environment]::GetFolderPath('Desktop')
        $lnk = Join-Path $desktop 'Agetha.lnk'
        $target = Join-Path $Script:Root 'Medic_Checker.bat'
        $icon = Join-Path $Script:Root 'assets\icon.ico'
        $shell = New-Object -ComObject WScript.Shell
        $sc = $shell.CreateShortcut($lnk)
        $sc.TargetPath = $target
        $sc.WorkingDirectory = $Script:Root
        $sc.Description = 'Agetha AI Companion'
        if (Test-Path -LiteralPath $icon) { $sc.IconLocation = $icon }
        $sc.Save()
        Write-Ok "Desktop shortcut: $lnk"
        Write-Info 'Start Menu Agetha.lnk uses AppUserModelID Agetha.Desktop for branded toasts.'
    } catch {
        Write-Warn "Could not create desktop shortcut: $_"
    }
}

function Wait-Key {
    Write-Host ''
    Read-Host 'Press Enter to continue'
}

function Test-ConfigYes {
    param([string]$Key, [string]$Default = 'yes')
    $v = Get-ConfigValue -Key $Key -Default $Default
    return $v -match '^(?i)(yes|true|1|on)$'
}

function Invoke-PythonHelper {
    param(
        [Parameter(Mandatory)][string]$PythonExe,
        [Parameter(Mandatory)][string]$Command
    )
    $helper = Join-Path $Script:Root 'medic_helper.py'
    $output = & $PythonExe $helper $Command 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return ($output | Select-Object -Last 1).ToString().Trim()
}

function Get-PythonArchitectureInfo {
    param([string]$PythonExe)
    try {
        $json = Invoke-PythonHelper -PythonExe $PythonExe -Command 'python_arch'
        if ($json) { return ($json | ConvertFrom-Json) }
    } catch { }
    try {
        $out = & $PythonExe -c 'import platform; print(platform.machine())' 2>$null
        if ($LASTEXITCODE -eq 0) {
            return [PSCustomObject]@{
                python_arch = ($out | Select-Object -Last 1).ToString().Trim()
                native_arch = 'UNKNOWN'
                reported_machine = ($out | Select-Object -Last 1).ToString().Trim()
                pointer_bits = 0
            }
        }
    } catch { }
    return $null
}

function Get-PythonMachine {
    param([string]$PythonExe)
    $info = Get-PythonArchitectureInfo -PythonExe $PythonExe
    if ($info) { return $info.python_arch }
    return $null
}

function Test-X64PythonArchitecture {
    param([string]$Architecture)
    return $Architecture -match '^(?i)(AMD64|x86_64|x64)$'
}

function Resolve-PythonCommand {
    if ($script:PreferredPythonExe -and (Test-Path -LiteralPath $script:PreferredPythonExe)) {
        $null = & $script:PreferredPythonExe --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            return @{ Exe = $script:PreferredPythonExe; Args = @() }
        }
    }
    if ($script:RequireX64Python) {
        $x64Dir = Find-X64PythonPath
        if ($x64Dir) {
            $script:PreferredPythonExe = Join-Path $x64Dir 'python.exe'
            return @{ Exe = $script:PreferredPythonExe; Args = @() }
        }
        return $null
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $null = & python --version 2>&1
        if ($LASTEXITCODE -eq 0) { return @{ Exe = 'python'; Args = @() } }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $null = & py -3 --version 2>&1
        if ($LASTEXITCODE -eq 0) { return @{ Exe = 'py'; Args = @('-3') } }
    }
    return $null
}

function Invoke-Python {
    param(
        [hashtable]$Cmd,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest
    )
    & $Cmd.Exe @($Cmd.Args + $Rest)
}

function Test-Arm64Host {
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
        Write-Info 'Method 1: PROCESSOR_ARCHITECTURE = ARM64'
        return $true
    }
    if ($env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') {
        Write-Info 'Method 2: PROCESSOR_ARCHITEW6432 = ARM64'
        return $true
    }
    Write-Info 'Methods 1 and 2 inconclusive - running OSArchitecture check...'
    $osArch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
    if ($osArch -eq [System.Runtime.InteropServices.Architecture]::Arm64) {
        Write-Info 'Method 3: OSArchitecture = Arm64'
        return $true
    }
    return $false
}

function Get-PythonExecutableCandidates {
    $candidates = [System.Collections.Generic.List[string]]::new()

    foreach ($command in @(Get-Command python -All -ErrorAction SilentlyContinue)) {
        $path = if ($command.Source) { $command.Source } else { $command.Path }
        if ($path) { $candidates.Add($path) }
    }

    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($line in @(& py -0p 2>$null)) {
            if ($line -match '([A-Za-z]:\\.+?python(?:w)?\.exe)\s*$') {
                $candidates.Add($Matches[1])
            }
        }
    }

    foreach ($registryRoot in @(
        'HKCU:\Software\Python\PythonCore',
        'HKLM:\Software\Python\PythonCore',
        'HKLM:\Software\WOW6432Node\Python\PythonCore'
    )) {
        foreach ($versionKey in @(Get-ChildItem -LiteralPath $registryRoot -ErrorAction SilentlyContinue)) {
            try {
                $install = Get-ItemProperty -LiteralPath (Join-Path $versionKey.PSPath 'InstallPath') -ErrorAction Stop
                if ($install.ExecutablePath) { $candidates.Add([string]$install.ExecutablePath) }
                $installDir = [string]$install.'(default)'
                if ($installDir) { $candidates.Add((Join-Path $installDir 'python.exe')) }
            } catch { }
        }
    }

    foreach ($installRoot in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python'),
        (Join-Path $env:ProgramFiles 'Python'),
        (Join-Path ${env:ProgramFiles(x86)} 'Python'),
        'C:\'
    )) {
        if (-not $installRoot -or -not (Test-Path -LiteralPath $installRoot)) { continue }
        foreach ($dir in @(Get-ChildItem -LiteralPath $installRoot -Directory -ErrorAction SilentlyContinue)) {
            if ($installRoot -eq 'C:\' -and $dir.Name -notlike 'Python*') { continue }
            $exe = Join-Path $dir.FullName 'python.exe'
            if (Test-Path -LiteralPath $exe) { $candidates.Add($exe) }
        }
    }

    return @($candidates | Where-Object {
        $_ -and (Test-Path -LiteralPath $_)
    } | Select-Object -Unique)
}

function Find-X64PythonPath {
    foreach ($exe in @(Get-PythonExecutableCandidates)) {
        $arch = Get-PythonMachine -PythonExe $exe
        if (Test-X64PythonArchitecture -Architecture $arch) {
            return (Split-Path -Parent $exe)
        }
    }
    return $null
}

function Install-X64Python {
    param([string]$PyVer = '3.13.3')

    $installerName = "python-$PyVer-amd64.exe"
    $installerPath = Join-Path $env:TEMP $installerName
    $url = "https://www.python.org/ftp/python/$PyVer/$installerName"

    Write-Head '+----------------------------------------------------------+'
    Write-Head '|  [D]  Automated Python x64 Installation Pipeline          |'
    Write-Head '+----------------------------------------------------------+'
    Write-Host ''

    Write-Step '[D1/4]  Downloading Python x64 from python.org...'
    Write-Info $url
    $ProgressPreference = 'SilentlyContinue'
    try {
        Invoke-WebRequest -Uri $url -OutFile $installerPath -UseBasicParsing
    } catch {
        Write-Fail "Download failed: $($_.Exception.Message)"
        Wait-Key
        exit 1
    }
    Write-Ok '[D1/4]  Download complete.'
    Write-Host ''

    Write-Step '[D2/4]  Verifying downloaded installer...'
    if (-not (Test-Path -LiteralPath $installerPath)) {
        Write-Fail 'Installer file not found after download.'
        Wait-Key
        exit 1
    }
    $bytes = (Get-Item -LiteralPath $installerPath).Length
    if ($bytes -lt 20000000) {
        Write-Fail "Installer appears corrupt ($bytes bytes, expected ~26 MB)."
        Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue
        Wait-Key
        exit 1
    }
    Write-Ok "[D2/4]  File verified - size: $bytes bytes."
    Write-Host ''

    Write-Step '[D3/4]  Running Python installer silently - please wait...'
    $proc = Start-Process -FilePath $installerPath -ArgumentList @(
        '/quiet', 'PrependPath=1', 'InstallAllUsers=0', 'Include_launcher=1'
    ) -Wait -PassThru
    Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue

    if ($proc.ExitCode -ne 0) {
        Write-Fail "Installer exited with code $($proc.ExitCode)."
        Wait-Key
        exit 1
    }
    Write-Ok "[D3/4]  Installation complete.  Exit code: $($proc.ExitCode)."
    Write-Host ''

    Write-Step '[D4/4]  Refreshing session PATH from registry...'
    $machinePath = [Environment]::GetEnvironmentVariable('PATH', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('PATH', 'User')
    $env:PATH = "$machinePath;$userPath"

    $x64Path = Find-X64PythonPath
    if (-not $x64Path) {
        Write-Warn 'The x64 installer completed, but its interpreter could not be located.'
        Write-Info 'Close this window, reopen it, and run this script again.'
        Wait-Key
        exit 1
    }
    $script:PreferredPythonExe = Join-Path $x64Path 'python.exe'
    $env:PATH = "$x64Path;$x64Path\Scripts;$env:PATH"
    Write-Ok "[D4/4]  Selected x64 Python: $script:PreferredPythonExe"

    $venvPy = Join-Path $Script:Root 'venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPy) {
        $venvArch = Get-PythonMachine -PythonExe $venvPy
        if ($venvArch -and -not (Test-X64PythonArchitecture -Architecture $venvArch)) {
            Write-Warn "Existing venv was built with $venvArch Python (incompatible)."
            Write-Step 'Removing stale venv - it will be rebuilt in step [2/7]...'
            Remove-Item -LiteralPath (Join-Path $Script:Root 'venv') -Recurse -Force
            Write-Ok 'Stale venv removed.'
        }
    }
    Write-Host ''
}

function Offer-Arm64Repair {
    param([string]$PyArch)

    Write-Line '+============================================================+' 'Red'
    Write-Line '|   CRITICAL CONFIGURATION HAZARD - ACTION REQUIRED          |' 'Red'
    Write-Line '+============================================================+' 'Red'
    Write-Host ''
    Write-Warn "ARM64 host - no compatible x64 Python found."
    Write-Info "Detected Python architecture: $PyArch"
    Write-Host ''
    Write-Head 'Why x64 Python is required on Snapdragon / ARM64 systems:'
    Write-Host ''
    Write-Info 'Agetha depends on: pygame-ce, pyautogui, mss, pillow'
    Write-Info 'PyPI ships x64 wheels only - ARM64 Python cannot install them.'
    Write-Info 'x64 Python runs under Windows Prism with no practical impact.'
    Write-Host ''

    $pyVer = '3.13.3'
    Write-Step "Proposed remedy: install Python $pyVer x64 (current user, no admin)."
    Write-Host ''
    $choice = Read-Host 'Download and install Python x64 now? [Y/N]'
    if ($choice -match '^(?i)y(es)?$') {
        Install-X64Python -PyVer $pyVer
        return
    }

    Write-Fail 'Automated installation declined.'
    Write-Info 'Manual steps: https://www.python.org/downloads/windows/'
    Write-Info 'Choose "Windows installer (64-bit)", tick "Add Python to PATH", re-run.'
    Wait-Key
    exit 1
}

function Get-PythonVersionInfo {
    param([hashtable]$Cmd)
    if (-not $Cmd) { return $null }
    $verOut = (Invoke-Python $Cmd --version 2>&1 | Out-String).Trim()
    if ($verOut -match 'Python\s+(\d+)\.(\d+)(?:\.(\d+))?') {
        $patch = 0
        if ($Matches[3]) { $patch = [int]$Matches[3] }
        return @{
            Major = [int]$Matches[1]
            Minor = [int]$Matches[2]
            Patch = $patch
            Text  = $verOut
        }
    }
    return $null
}

function Test-IsSupportedPythonVersion {
    # Hard floor/ceiling: below 3.10 or above 3.14 cannot run Agetha reliably.
    param([hashtable]$VersionInfo)
    if (-not $VersionInfo) { return $false }
    if ($VersionInfo.Major -ne 3) { return $false }
    return ($VersionInfo.Minor -ge 10 -and $VersionInfo.Minor -le 14)
}

function Test-IsRecommendedPythonVersion {
    # 3.10-3.13 remain the most battle-tested; 3.14 works with pygame-ce.
    param([hashtable]$VersionInfo)
    if (-not $VersionInfo) { return $false }
    if ($VersionInfo.Major -ne 3) { return $false }
    return ($VersionInfo.Minor -ge 10 -and $VersionInfo.Minor -le 13)
}

function Test-PygameSatisfied {
    param([Parameter(Mandatory)][string]$PythonExe)
    # pygame-ce installs as importable 'pygame'; pip show lists pygame-ce.
    $null = & $PythonExe -c 'import pygame' 2>&1
    if ($LASTEXITCODE -eq 0) { return $true }
    $null = & $PythonExe -m pip show pygame-ce 2>&1
    if ($LASTEXITCODE -eq 0) { return $true }
    $null = & $PythonExe -m pip show pygame 2>&1
    if ($LASTEXITCODE -eq 0) { return $true }
    return $false
}

function Remove-AgethaVenv {
    $venvDir = Join-Path $Script:Root 'venv'
    if (Test-Path -LiteralPath $venvDir) {
        Write-Step 'Removing stale venv - it will be rebuilt in step [2/7]...'
        Remove-Item -LiteralPath $venvDir -Recurse -Force
        Write-Ok 'Stale venv removed.'
    }
}

function Offer-UnsupportedPythonRepair {
    param(
        [Parameter(Mandatory)][hashtable]$VersionInfo,
        [string]$Reason = '',
        [switch]$ExitAfterRepair
    )

    Write-Line '+============================================================+' 'Red'
    Write-Line '|   UNSUPPORTED / BROKEN PYTHON SETUP - ACTION REQUIRED      |' 'Red'
    Write-Line '+============================================================+' 'Red'
    Write-Host ''
    Write-Fail "Detected $($VersionInfo.Text)"
    if ($Reason) { Write-Info $Reason }
    Write-Host ''
    Write-Head 'Why package install fails on this Python:'
    Write-Host ''
    Write-Info 'Agetha uses pygame-ce (provides import pygame). Some other packages may'
    Write-Info 'still lack wheels on very new Python builds. Recommended: Python 3.13.x.'
    Write-Info 'If install fails, try: pip install pygame-ce  then pip install -r requirements.txt'
    Write-Host ''

    $pyVer = '3.13.3'
    Write-Step "Proposed remedy: install Python $pyVer x64 (current user, no admin)."
    Write-Info 'Recommended for Agetha: Python 3.13.x (best wheel coverage).'
    Write-Host ''
    $choice = Read-Host 'Download and install Python 3.13.3 x64 now? [Y/N]'
    if ($choice -match '^(?i)y(es)?$') {
        Install-X64Python -PyVer $pyVer

        $py313 = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313'
        $py313Exe = Join-Path $py313 'python.exe'
        if (Test-Path -LiteralPath $py313Exe) {
            Write-Step 'Preferring Python 3.13 on session PATH...'
            $env:PATH = "$py313;$py313\Scripts;$env:PATH"
        }

        Remove-AgethaVenv

        $script:PythonCmd = Resolve-PythonCommand
        if (-not $script:PythonCmd) {
            Write-Fail 'Python not found on PATH after install.'
            Write-Info 'Close this window, reopen it, and run this script again.'
            Wait-Key
            exit 1
        }
        $newVer = Get-PythonVersionInfo -Cmd $script:PythonCmd
        if ((-not $newVer) -or (-not (Test-IsRecommendedPythonVersion -VersionInfo $newVer))) {
            $shown = '(unknown)'
            if ($newVer -and $newVer.Text) { $shown = $newVer.Text }
            Write-Fail "Still not on Python 3.13 after install: $shown"
            Write-Info 'Close this window, ensure Python 3.13 is first on PATH, then re-run.'
            Write-Info 'Manual: https://www.python.org/downloads/windows/ (64-bit 3.13.x)'
            Wait-Key
            exit 1
        }
        $script:PythonVersionInfo = $newVer
        Write-Ok "Now using $($newVer.Text) (recommended)."
        Write-Host ''
        if ($ExitAfterRepair) {
            Write-Ok 'Python 3.13 installed and old venv removed.'
            Write-Info 'Close this window and run Medic_Checker.ps1 again to finish setup.'
            Wait-Key
            exit 0
        }
        return
    }

    Write-Fail 'Automated installation declined - cannot continue with this Python setup.'
    Write-Info 'Manual steps:'
    Write-Info '  1. Install Python 3.13.x x64: https://www.python.org/downloads/windows/'
    Write-Info '  2. Tick "Add python.exe to PATH" during install'
    Write-Info '  3. Delete the project venv folder, then re-run Medic_Checker.ps1'
    Write-Info '  Or: pip install pygame-ce  then pip install -r requirements.txt'
    Wait-Key
    exit 1
}

function Write-PipFailureTail {
    param([string]$PipOutput)
    if (-not $PipOutput) { return }
    $lines = @($PipOutput -split "`r?`n" | Where-Object { $_.Trim() -ne '' })
    if ($lines.Count -eq 0) { return }
    $start = [Math]::Max(0, $lines.Count - 12)
    Write-Info '--- pip output (last lines) ---'
    for ($i = $start; $i -lt $lines.Count; $i++) {
        Write-Info $lines[$i]
    }
}

function Install-PygameResilient {
    param([Parameter(Mandatory)][string]$PythonExe)

    Write-Step 'Installing pygame-ce (preferred; provides import pygame)...'
    $outCe = & $PythonExe -m pip install 'pygame-ce>=2.5.6,<3.0.0' --only-binary=:all: --disable-pip-version-check 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        Write-Ok 'pygame-ce installed.'
        return 'pygame-ce'
    }
    Write-Info 'pygame-ce binary-only install failed - retrying without --only-binary...'
    Write-PipFailureTail -PipOutput $outCe
    $outCe2 = & $PythonExe -m pip install 'pygame-ce>=2.5.6,<3.0.0' --disable-pip-version-check 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        Write-Ok 'pygame-ce installed.'
        return 'pygame-ce'
    }
    Write-PipFailureTail -PipOutput $outCe2

    Write-Info 'pygame-ce failed - last resort: classic pygame binary wheel only...'
    $outA = & $PythonExe -m pip install 'pygame>=2.5.0,<3.0.0' --only-binary=:all: --disable-pip-version-check 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        Write-Warn 'classic pygame installed (prefer pygame-ce when available).'
        return 'pygame'
    }
    Write-PipFailureTail -PipOutput $outA
    return $null
}

function New-RequirementsWithoutPygame {
    param([Parameter(Mandatory)][string]$RequirementsPath)
    $tempPath = Join-Path $env:TEMP 'agetha_requirements_no_pygame.txt'
    $filtered = @(Get-Content -LiteralPath $RequirementsPath | Where-Object {
        $_ -notmatch '^\s*pygame(-ce)?(\s*[><=!]|\s*$)'
    })
    Set-Content -LiteralPath $tempPath -Value $filtered -Encoding UTF8
    return $tempPath
}

function Test-Arm64Python {
    Write-Head '+----------------------------------------------------------+'
    Write-Head '|  [B]  Python Variant Evaluation  (ARM64 host)             |'
    Write-Head '+----------------------------------------------------------+'
    Write-Host ''

    $pyArch = 'MISSING'
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $pathCommand = Get-Command python -ErrorAction SilentlyContinue
        $pathPython = if ($pathCommand.Source) { $pathCommand.Source } else { $pathCommand.Path }
        $info = Get-PythonArchitectureInfo -PythonExe $pathPython
        if ($info) {
            $pyArch = $info.python_arch
            Write-Info "Python on PATH - interpreter architecture: $pyArch"
            Write-Info "Native Windows architecture: $($info.native_arch)"
            if (Test-X64PythonArchitecture -Architecture $pyArch) {
                $script:PreferredPythonExe = $pathPython
            }
        }
    } else {
        Write-Info 'No Python binary found on PATH.'
    }

    if (Test-X64PythonArchitecture -Architecture $pyArch) {
        Write-Ok 'Python is x64 (AMD64) - running under Prism emulation.'
        Write-Ok 'Binary wheels for pygame-ce / pyautogui / mss install correctly.'
        Write-Host ''
        return
    }

    if ($pyArch -match '^(?i)(ARM64|aarch64)$') {
        Write-Warn 'The Python first on PATH is ARM64; checking other installed interpreters.'
    } else {
        Write-Warn 'No compatible Python found on PATH.'
    }

    Write-Step 'Scanning known install directories for an existing x64 build...'
    Write-Host ''
    $x64Path = Find-X64PythonPath
    if ($x64Path) {
        Write-Ok "Found existing x64 Python at: $x64Path"
        $script:PreferredPythonExe = Join-Path $x64Path 'python.exe'
        Write-Step 'Selecting it for venv creation - no download required.'
        $env:PATH = "$x64Path;$x64Path\Scripts;$env:PATH"
        Write-Ok "x64 Python selected: $script:PreferredPythonExe"
        Write-Host ''
        return
    }

    Offer-Arm64Repair -PyArch $pyArch
}

function Get-ConfigStatus {
    $helper = Join-Path $Script:Root 'medic_helper.py'
    $venvPy = Join-Path $Script:Root 'venv\Scripts\python.exe'
    $py = if (Test-Path -LiteralPath $venvPy) { $venvPy } else { 'python' }

    if (Test-Path -LiteralPath (Join-Path $Script:Root '.env')) {
        $envStatus = Invoke-PythonHelper -PythonExe $py -Command 'env'
        # medic_helper env prints SET (Groq) or OPENROUTER — both mean a usable key
        if ($envStatus -eq 'SET' -or $envStatus -eq 'OPENROUTER') { return $envStatus }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Script:Root 'config.txt'))) {
        return 'NO_CONFIG'
    }
    return Invoke-PythonHelper -PythonExe $py -Command 'config'
}

function Invoke-StandardChecks {
    $script:SkipTesseractCheck = (Get-ConfigValue -Key 'SKIP_TESSERACT_CHECK' -Default 'no') -match '^(?i)yes$'
    $script:SkipAssetCheck = (Get-ConfigValue -Key 'SKIP_ASSET_CHECK' -Default 'no') -match '^(?i)yes$'
    $script:AutoPipInstall = Test-ConfigYes -Key 'AUTO_PIP_INSTALL' -Default 'yes'

    Write-Head '+----------------------------------------------------------+'
    Write-Head '|  Standard System Health Checks  [1/7 - 7/7]              |'
    Write-Head '+----------------------------------------------------------+'
    Write-Host ''

    # [1/7] Python
    Write-Head '[1 / 7]  Python'
    $script:PythonCmd = Resolve-PythonCommand
    if (-not $script:PythonCmd) {
        if ($script:RequireX64Python) {
            Write-Fail 'No compatible x64 Python installation could be selected.'
        } else {
            Write-Fail 'Python not found on PATH.'
        }
        Write-Info 'https://www.python.org/downloads/'
        Wait-Key
        exit 1
    }
    $script:PythonVersionInfo = Get-PythonVersionInfo -Cmd $script:PythonCmd
    if ($script:PythonVersionInfo) {
        Write-Ok $script:PythonVersionInfo.Text
        if (-not (Test-IsSupportedPythonVersion -VersionInfo $script:PythonVersionInfo)) {
            Offer-UnsupportedPythonRepair -VersionInfo $script:PythonVersionInfo `
                -Reason 'Agetha supports Python 3.10 through 3.14 (3.13 recommended).'
        } elseif (Test-IsRecommendedPythonVersion -VersionInfo $script:PythonVersionInfo) {
            Write-Ok 'Python version is recommended (3.10-3.13).'
        } else {
            Write-Warn 'Python 3.14 detected - most packages should work with pygame-ce.'
            Write-Info 'If installs fail, prefer Python 3.13.x for best wheel coverage.'
        }
    } else {
        $ver = Invoke-Python $script:PythonCmd --version 2>&1 | Out-String
        Write-Warn "Could not parse Python version: $($ver.Trim())"
        Write-Info 'Continuing - package install may fail if binary wheels are missing for this build.'
    }
    Write-Host ''

    # [2/7] venv
    Write-Head '[2 / 7]  Virtual environment'
    $venvDir = Join-Path $Script:Root 'venv'
    $venvPy = Join-Path $venvDir 'Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPy) {
        if ($script:RequireX64Python) {
            $venvArch = Get-PythonMachine -PythonExe $venvPy
            if ($venvArch -and -not (Test-X64PythonArchitecture -Architecture $venvArch)) {
                Write-Warn "Existing venv uses $venvArch Python; rebuilding it with x64 Python."
                Remove-Item -LiteralPath $venvDir -Recurse -Force
            }
        }
    }
    if (Test-Path -LiteralPath $venvPy) {
        $venvVerInfo = Get-PythonVersionInfo -Cmd @{ Exe = $venvPy; Args = @() }
        if ($venvVerInfo -and -not (Test-IsSupportedPythonVersion -VersionInfo $venvVerInfo)) {
            Write-Warn "Existing venv uses unsupported $($venvVerInfo.Text)"
            $sysVer = Get-PythonVersionInfo -Cmd $script:PythonCmd
            if ($sysVer -and (Test-IsSupportedPythonVersion -VersionInfo $sysVer)) {
                Write-Step "Rebuilding venv with $($sysVer.Text)..."
                Remove-Item -LiteralPath $venvDir -Recurse -Force
            } else {
                Offer-UnsupportedPythonRepair -VersionInfo $venvVerInfo
            }
        }
    }
    if (-not (Test-Path -LiteralPath $venvPy)) {
        Write-Step 'Not found - creating venv (first run only)...'
        Invoke-Python $script:PythonCmd -m venv $venvDir
        if ($LASTEXITCODE -ne 0) {
            Write-Fail 'venv creation failed. Check write permissions.'
            Wait-Key
            exit 1
        }
        Write-Ok 'venv created.'
    } else {
        Write-Ok 'venv already exists.'
    }
    $script:VenvPython = $venvPy
    Write-Ok 'venv ready.'
    Write-Host ''

    # [3/7] packages
    Write-Head '[3 / 7]  Python packages (requirements.txt)'
    $reqPath = Join-Path $Script:Root 'requirements.txt'
    $required = @('pillow', 'pyautogui', 'pytesseract', 'numpy', 'pygame-ce', 'requests', 'groq', 'mss', 'psutil')
    $missing = @()
    foreach ($pkg in $required) {
        if ($pkg -eq 'pygame-ce') {
            if (-not (Test-PygameSatisfied -PythonExe $script:VenvPython)) { $missing += $pkg }
        } else {
            $null = & $script:VenvPython -m pip show $pkg 2>&1
            if ($LASTEXITCODE -ne 0) { $missing += $pkg }
        }
    }
    if ($missing.Count -eq 0) {
        Write-Ok 'All required packages installed.'
    } elseif (-not $script:AutoPipInstall) {
        Write-Warn "Missing: $($missing -join ', ')"
        Write-Warn 'AUTO_PIP_INSTALL=no - run: pip install -r requirements.txt'
        Write-Info 'Audio: pip install "pygame-ce>=2.5.6,<3.0.0"'
    } else {
        Write-Step "Missing: $($missing -join ', ')"
        Write-Step 'Installing from requirements.txt - please wait...'
        $pygameProvider = $null
        $pipOut = & $script:VenvPython -m pip install -r $reqPath --disable-pip-version-check 2>&1 | Out-String

        if ($LASTEXITCODE -ne 0) {
            Write-Warn 'Full requirements install failed.'
            Write-PipFailureTail -PipOutput $pipOut

            $needPygame = ($missing -contains 'pygame-ce') -or (-not (Test-PygameSatisfied -PythonExe $script:VenvPython))
            if ($needPygame -and -not (Test-PygameSatisfied -PythonExe $script:VenvPython)) {
                $pygameProvider = Install-PygameResilient -PythonExe $script:VenvPython
                if (-not $pygameProvider) {
                    Write-Fail 'pygame-ce could not be installed.'
                    if ($script:PythonVersionInfo) {
                        Offer-UnsupportedPythonRepair -VersionInfo $script:PythonVersionInfo `
                            -Reason 'No pygame-ce (or classic pygame) wheel for this Python.' `
                            -ExitAfterRepair
                    } else {
                        Write-Info 'Install Python 3.13.x x64, delete venv, re-run Medic_Checker.ps1'
                        Write-Info 'Download: https://www.python.org/downloads/windows/'
                        Wait-Key
                        exit 1
                    }
                }
            }

            Write-Step 'Retrying remaining packages from requirements.txt...'
            $retryPath = $reqPath
            $tempReq = $null
            if ($pygameProvider) {
                $tempReq = New-RequirementsWithoutPygame -RequirementsPath $reqPath
                $retryPath = $tempReq
                Write-Info 'Excluding pygame/pygame-ce pin (audio package already installed).'
            }
            $pipOut2 = & $script:VenvPython -m pip install -r $retryPath --disable-pip-version-check 2>&1 | Out-String
            if ($tempReq) {
                Remove-Item -LiteralPath $tempReq -Force -ErrorAction SilentlyContinue
            }
            if ($LASTEXITCODE -ne 0) {
                Write-Fail 'Package install failed after pygame-ce fallback.'
                Write-PipFailureTail -PipOutput $pipOut2
                if ($script:PythonVersionInfo) {
                    Offer-UnsupportedPythonRepair -VersionInfo $script:PythonVersionInfo `
                        -Reason 'requirements.txt still failed after pygame-ce fallback.' `
                        -ExitAfterRepair
                } else {
                    Write-Info 'Try manually: pip install pygame-ce && pip install -r requirements.txt'
                    Write-Info 'Or install Python 3.13.x and rebuild venv.'
                    Wait-Key
                    exit 1
                }
            }
        }

        $stillMissing = @()
        foreach ($pkg in $required) {
            if ($pkg -eq 'pygame-ce') {
                if (-not (Test-PygameSatisfied -PythonExe $script:VenvPython)) { $stillMissing += $pkg }
            } else {
                $null = & $script:VenvPython -m pip show $pkg 2>&1
                if ($LASTEXITCODE -ne 0) { $stillMissing += $pkg }
            }
        }
        if ($stillMissing.Count -gt 0) {
            Write-Fail "Still missing after install: $($stillMissing -join ', ')"
            if ($script:PythonVersionInfo -and ($stillMissing -contains 'pygame-ce')) {
                Offer-UnsupportedPythonRepair -VersionInfo $script:PythonVersionInfo `
                    -Reason "Still missing: $($stillMissing -join ', ')" `
                    -ExitAfterRepair
            } else {
                Write-Info 'Run: pip install -r requirements.txt'
                Wait-Key
                exit 1
            }
        }
        Write-Ok 'Packages installed (audio via pygame-ce).'
    }
    $null = & $script:VenvPython -m pip show tkextrafont 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Info 'tkextrafont optional - not installed (Barrio font fallback used).'
    } else {
        Write-Ok 'tkextrafont optional package present.'
    }

    # Optional: voice, local STT, drag-and-drop, TTS (driven by config.txt)
    Write-Step 'Optional features (voice / drag-and-drop / TTS)...'
    $enableVoice = (Get-ConfigValue -Key 'ENABLE_VOICE' -Default 'no') -match '^(?i)yes$'
    $useLocalStt = (Get-ConfigValue -Key 'USE_LOCAL_STT' -Default 'no') -match '^(?i)yes$'
    $enableDnd = (Get-ConfigValue -Key 'ENABLE_FILE_DRAG_DROP' -Default 'yes') -match '^(?i)yes$'
    $voiceOutputMode = (Get-ConfigValue -Key 'VOICE_OUTPUT_MODE' -Default 'bleeps_only').ToLower()
    $voiceTtsEngine = (Get-ConfigValue -Key 'VOICE_TTS_ENGINE' -Default 'pyttsx3').ToLower()
    if ($voiceTtsEngine -notin @('pyttsx3', 'edge_tts', 'kokoro')) { $voiceTtsEngine = 'pyttsx3' }
    $needsTts = $voiceOutputMode -in @('tts_only', 'both')
    $optionalPkgs = @()
    if ($enableVoice) {
        $optionalPkgs += 'SpeechRecognition', 'PyAudio'
        if ($useLocalStt) { $optionalPkgs += 'faster-whisper' }
    }
    if ($enableDnd) { $optionalPkgs += 'tkinterdnd2' }
    if ($needsTts) {
        if ($voiceTtsEngine -eq 'edge_tts') {
            $optionalPkgs += 'edge-tts'
        } elseif ($voiceTtsEngine -eq 'kokoro') {
            $optionalPkgs += 'kokoro', 'soundfile'
        } else {
            $optionalPkgs += 'pyttsx3'
        }
    }
    # @() keeps a single package as a 1-element array (pipeline unwraps scalars; breaks .Count in StrictMode)
    $optionalPkgs = @($optionalPkgs | Select-Object -Unique)
    if ($optionalPkgs.Count -eq 0) {
        Write-Info 'ENABLE_VOICE=no, ENABLE_FILE_DRAG_DROP=no, VOICE_OUTPUT_MODE=bleeps_only - optional packages skipped.'
    } else {
        $optMissing = @()
        foreach ($pkg in $optionalPkgs) {
            $null = & $script:VenvPython -m pip show $pkg 2>&1
            if ($LASTEXITCODE -ne 0) { $optMissing += $pkg }
        }
        if ($optMissing.Count -eq 0) {
            Write-Ok "Optional packages ready: $($optionalPkgs -join ', ')"
        } elseif ($script:AutoPipInstall) {
            Write-Step "Installing optional: $($optMissing -join ', ')"
            & $script:VenvPython -m pip install @optMissing --quiet --disable-pip-version-check
            if ($LASTEXITCODE -eq 0) {
                Write-Ok 'Optional packages installed.'
            } else {
                Write-Warn "Optional install failed for: $($optMissing -join ', ')"
                Write-Info 'Voice: ENABLE_VOICE=yes needs SpeechRecognition + PyAudio'
                Write-Info 'Local STT: USE_LOCAL_STT=yes needs faster-whisper (~75 MB model on first run)'
                Write-Info 'Drag-drop: ENABLE_FILE_DRAG_DROP=yes needs tkinterdnd2 (Windows)'
                Write-Info 'TTS: VOICE_OUTPUT_MODE=tts_only|both needs package for VOICE_TTS_ENGINE'
                Write-Info '  pyttsx3  -> pip install "pyttsx3>=2.90,<3.0.0"'
                Write-Info '  edge_tts -> pip install "edge-tts>=6.1.0,<8.0.0"'
                Write-Info '  kokoro   -> pip install "kokoro>=0.9.4" soundfile  (needs espeak-ng on PATH)'
            }
        } else {
            Write-Warn "Optional missing: $($optMissing -join ', ')"
            Write-Info 'Set AUTO_PIP_INSTALL=yes or: pip install -r requirements.txt'
        }
    }
    if ($needsTts) {
        $ttsStatus = Invoke-PythonHelper -PythonExe $script:VenvPython -Command 'tts'
        if ($ttsStatus -like 'TTS_OK*') {
            Write-Ok "TTS ($voiceTtsEngine) ready for VOICE_OUTPUT_MODE=$voiceOutputMode."
        } elseif ($ttsStatus -like 'TTS_MISSING*') {
            Write-Warn "VOICE_OUTPUT_MODE=$voiceOutputMode VOICE_TTS_ENGINE=$voiceTtsEngine - package missing; falls back to bleeps."
            if ($voiceTtsEngine -eq 'edge_tts') {
                Write-Info 'Install: pip install "edge-tts>=6.1.0,<8.0.0"'
            } elseif ($voiceTtsEngine -eq 'kokoro') {
                Write-Info 'Install: pip install "kokoro>=0.9.4" soundfile'
                Write-Info 'Also install espeak-ng and add it to PATH (required by Kokoro).'
            } else {
                Write-Info 'Install: pip install "pyttsx3>=2.90,<3.0.0"'
            }
        } else {
            Write-Info "VOICE_OUTPUT_MODE=$voiceOutputMode (TTS check: $ttsStatus)"
        }
    } else {
        Write-Ok 'VOICE_OUTPUT_MODE=bleeps_only - TTS optional package skipped.'
    }
    if ($enableVoice) {
        $voiceLines = & $script:VenvPython medic_helper.py voice 2>&1
        $voiceText = ($voiceLines | Out-String).Trim()
        if ($voiceText -match '^VOICE_OK') {
            Write-Ok 'Voice input dependencies OK.'
            if ($voiceText -match 'STT_OK') {
                Write-Ok 'Local STT (faster-whisper) ready.'
            } elseif ($useLocalStt -and $voiceText -match 'STT_MISSING') {
                Write-Warn 'USE_LOCAL_STT=yes but faster-whisper not installed.'
            } elseif (-not $useLocalStt) {
                Write-Ok 'STT mode: Google Speech Recognition (online).'
            }
        } else {
            Write-Warn "Voice not ready: $voiceText"
        }
    }
    if ($enableDnd) {
        $dndStatus = Invoke-PythonHelper -PythonExe $script:VenvPython -Command 'dnd'
        if ($dndStatus -eq 'DND_OK') {
            Write-Ok 'File drag-and-drop (tkinterdnd2) ready.'
        } else {
            Write-Warn 'tkinterdnd2 not installed - drag-and-drop disabled.'
        }
    }
    Write-Host ''

    # [4/7] Tesseract
    Write-Head '[4 / 7]  Tesseract OCR (screen reader)'
    if ($script:SkipTesseractCheck) {
        Write-Info 'SKIP_TESSERACT_CHECK=yes - step skipped.'
    } else {
        $customTess = Get-ConfigValue -Key 'TESSERACT_PATH' -Default ''
        $tessPaths = @(
            (Get-Command tesseract -ErrorAction SilentlyContinue)
            'C:\Program Files\Tesseract-OCR\tesseract.exe'
            'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe'
        )
        if ($customTess -and (Test-Path -LiteralPath $customTess)) {
            $tessPaths = @($customTess) + $tessPaths
        }
        $hasTess = $false
        foreach ($t in $tessPaths) {
            if ($t -is [System.Management.Automation.CommandInfo]) { $hasTess = $true; break }
            if ($t -and (Test-Path -LiteralPath $t)) { $hasTess = $true; break }
        }
        if ($hasTess) {
            Write-Ok 'Tesseract found - screen reading enabled.'
        } else {
            Write-Warn 'Tesseract not installed - screen reading disabled.'
            Write-Info 'Install: https://github.com/UB-Mannheim/tesseract/wiki'
            Write-Info 'Or set TESSERACT_PATH in config.txt to your tesseract.exe.'
        }
    }
    $deepOcrStatus = Invoke-PythonHelper -PythonExe $script:VenvPython -Command 'deep_ocr'
    if ($deepOcrStatus -eq 'DEEP_OCR_DISABLED') {
        Write-Info 'Deep OCR: disabled (optional).'
    } elseif ($deepOcrStatus -eq 'DEEP_OCR_CONFIGURED_LOCAL') {
        Write-Ok 'Deep OCR: configured for a local Unlimited-OCR service (connectivity not tested).'
    } elseif ($deepOcrStatus -eq 'DEEP_OCR_CONFIGURED_REMOTE') {
        Write-Warn 'Deep OCR: configured for a remote service; screenshots leave this machine when explicitly requested.'
    } elseif ($deepOcrStatus -eq 'DEEP_OCR_REMOTE_BLOCKED') {
        Write-Warn 'Deep OCR: remote URL blocked because UNLIMITED_OCR_ALLOW_REMOTE=no.'
    } else {
        Write-Warn 'Deep OCR: optional configuration has an invalid server URL.'
    }
    Write-Host ''

    # [5/7] assets
    Write-Head '[5 / 7]  Assets (assets\)'
    if ($script:SkipAssetCheck) {
        Write-Info 'SKIP_ASSET_CHECK=yes - step skipped.'
    } else {
    $assets = @(
        'angry-static.gif', 'angry.gif', 'error.gif', 'happy-static.gif', 'happy.gif',
        'icon.ico', 'idle-1.gif', 'idle-2.gif', 'idle-3.gif', 'loaf.gif',
        'sad-static.gif', 'sad.gif', 'sleeping.gif', 'surprised.gif',
        'talking-1.gif', 'talking-2.gif', 'talking-3.gif',
        'thinking-static.gif', 'thinking.gif', 'want.gif', 'barrio.ttf'
    )
    $assetFail = $false
    foreach ($name in $assets) {
        $path = Join-Path $Script:Root "assets\$name"
        if (-not (Test-Path -LiteralPath $path)) {
            Write-Warn "[MISS]  assets\$name"
            $assetFail = $true
        }
    }
    if (-not $assetFail) {
        Write-Ok "All $($assets.Count) assets present (all mood GIFs including want.gif + loaf/sleep presence)."
    } else {
        Write-Warn 'Missing assets will cause broken or invisible animations (want.gif, loaf.gif, sleeping.gif matter for presence).'
    }
    }
    Write-Host ''

    # [6/7] config
    Write-Head '[6 / 7]  Config, .env & memory'
    $memoryDir = Join-Path $Script:Root 'memory'
    if (-not (Test-Path -LiteralPath $memoryDir)) {
        New-Item -ItemType Directory -Path $memoryDir | Out-Null
        Write-Ok 'Created memory\'
    } else {
        Write-Ok 'memory\ exists.'
    }
    if (Test-Path -LiteralPath (Join-Path $memoryDir 'soul.md')) {
        Write-Ok 'memory\soul.md present.'
    } else {
        Write-Info 'memory\soul.md will be auto-generated on first run.'
    }
    $phase1Files = @(
        @{ Name = 'episodic_memory.json'; Label = 'episodic memory' }
        @{ Name = 'longterm_memory.jsonl'; Label = 'long-term searchable memory' }
        @{ Name = 'companion_stats.json'; Label = 'companion stats (Virus Registry)' }
        @{ Name = 'notepad.txt'; Label = 'dashboard notepad' }
        @{ Name = 'dreams.jsonl'; Label = 'dream journal (v4 — created on first deep sleep)' }
        @{ Name = 'tasks.json'; Label = 'task keeper (v4 — created on first add_task)' }
        @{ Name = 'emotional_state.json'; Label = 'emotion engine state (v5 — created on first event)' }
        @{ Name = 'emotional_history.jsonl'; Label = 'emotional history (v5 — created on first event)' }
        @{ Name = 'audit_log.jsonl'; Label = 'system-change audit log (v5 — created on first change)' }
    )
    foreach ($pf in $phase1Files) {
        $p = Join-Path $memoryDir $pf.Name
        if (Test-Path -LiteralPath $p) {
            Write-Ok "memory\$($pf.Name) present ($($pf.Label))."
        } else {
            Write-Info "memory\$($pf.Name) will be created on first use ($($pf.Label))."
        }
    }
    $enableLtMem = (Get-ConfigValue -Key 'ENABLE_LONGTERM_MEMORY' -Default 'yes') -match '^(?i)yes$'
    if ($enableLtMem) {
        Write-Ok 'ENABLE_LONGTERM_MEMORY=yes - search_memory + JSONL dual-write + session recap active.'
    } else {
        Write-Info 'ENABLE_LONGTERM_MEMORY=no - long-term search / session recap archive disabled.'
    }
    $enableStatsCtx = (Get-ConfigValue -Key 'ENABLE_COMPANION_STATS_CONTEXT' -Default 'yes') -match '^(?i)yes$'
    if ($enableStatsCtx) {
        Write-Ok 'ENABLE_COMPANION_STATS_CONTEXT=yes - host heat / infection persona context in prompts.'
    } else {
        Write-Info 'ENABLE_COMPANION_STATS_CONTEXT=no - companion stats not injected into AI prompts.'
    }
    $loafMin = Get-ConfigValue -Key 'LOAF_TIMER_MIN' -Default '15'
    Write-Info "Presence: LOAF_TIMER_MIN=$loafMin (idle → loaf → sleep; cosmetic only)."
    Write-Info "VOICE_OUTPUT_MODE=$(Get-ConfigValue -Key 'VOICE_OUTPUT_MODE' -Default 'bleeps_only')"
    $enableWebRag = (Get-ConfigValue -Key 'ENABLE_WEB_RAG' -Default 'no') -match '^(?i)yes$'
    if ($enableWebRag) {
        Write-Ok 'ENABLE_WEB_RAG=yes - search_web / fetch_webpage active (CAUTION confirmations).'
    } else {
        Write-Info 'ENABLE_WEB_RAG=no - web search/fetch disabled (safe default).'
    }
    $enableGlitch = (Get-ConfigValue -Key 'ENABLE_GLITCH_EFFECTS' -Default 'no') -match '^(?i)yes$'
    if ($enableGlitch) {
        Write-Ok 'ENABLE_GLITCH_EFFECTS=yes - visual glitch overlay allowed (cosmetic only).'
    } else {
        Write-Info 'ENABLE_GLITCH_EFFECTS=no - glitch overlay disabled (safe default).'
    }
    $enableConfirm = (Get-ConfigValue -Key 'ENABLE_COMMAND_CONFIRMATIONS' -Default 'yes') -match '^(?i)yes$'
    if ($enableConfirm) {
        Write-Ok 'ENABLE_COMMAND_CONFIRMATIONS=yes - Caution/Danger native Yes/No dialogs active.'
    } else {
        Write-Warn 'ENABLE_COMMAND_CONFIRMATIONS=no - risky OS actions will NOT prompt (not recommended).'
    }
    $enableCmdExec = (Get-ConfigValue -Key 'ENABLE_COMMAND_EXECUTION' -Default 'yes') -match '^(?i)yes$'
    if (-not $enableCmdExec) {
        Write-Info 'ENABLE_COMMAND_EXECUTION=no - all OS commands blocked (speak/idle still work).'
    }
    # v5.0.0 — live "Start Agetha when I sign in" status (read-only; never mutates)
    $autostartStatus = Invoke-PythonHelper -PythonExe $script:VenvPython -Command 'autostart'
    switch -Regex ($autostartStatus) {
        '^AUTOSTART_ON$' {
            Write-Ok 'Start Agetha when I sign in: ON (valid Startup-folder shortcut present).'
        }
        '^AUTOSTART_OFF$' {
            Write-Info 'Start Agetha when I sign in: OFF (no Startup shortcut).'
        }
        '^AUTOSTART_MALFORMED$' {
            Write-Warn 'Start Agetha when I sign in: malformed Agetha.lnk in Startup (left untouched).'
        }
        '^AUTOSTART_FOREIGN$' {
            Write-Warn 'Start Agetha when I sign in: foreign Agetha.lnk in Startup (left untouched).'
        }
        '^AUTOSTART_UNAVAILABLE$' {
            Write-Info 'Start Agetha when I sign in: unavailable (non-Windows).'
        }
        default {
            Write-Info "Start Agetha when I sign in: status check skipped ($autostartStatus)."
        }
    }
    $enableAutostartCtrl = (Get-ConfigValue -Key 'ENABLE_AUTOSTART_CONTROL' -Default 'no') -match '^(?i)yes$'
    if ($enableAutostartCtrl) {
        Write-Info 'ENABLE_AUTOSTART_CONTROL=yes - set_autostart command allowed (Danger confirmation still required).'
    } else {
        Write-Info 'ENABLE_AUTOSTART_CONTROL=no - set_autostart command disabled (safe default).'
    }
    $conv = Join-Path $Script:Root 'conversation.txt'
    if (-not (Test-Path -LiteralPath $conv)) {
        New-Item -ItemType File -Path $conv -Force | Out-Null
    }
    if (Test-Path -LiteralPath (Join-Path $Script:Root '.env.example')) {
        Write-Ok '.env.example present.'
    } else {
        Write-Warn '.env.example missing - copy template for API keys.'
    }

    $cfgStatus = Get-ConfigStatus
    switch ($cfgStatus) {
        'SET' {
            Write-Ok '.env - Groq API key configured.'
        }
        'LOCAL'       { Write-Ok 'config.txt - Local AI (Ollama) mode active.' }
        'OPENROUTER'  { Write-Ok '.env - OpenRouter API key configured.' }
        'LOCAL_NO_MODEL' {
            Write-Warn 'USE_LOCAL_AI=yes but LOCAL_AI_MODEL is blank.'
            Write-Info 'Run: ollama list  then set LOCAL_AI_MODEL in config.txt'
        }
        'EMPTY' {
            Write-Warn 'No API key in .env - Agetha will not respond.'
            Write-Info 'Free key: https://console.groq.com'
            Write-Info 'Required: copy .env.example to .env and add GROQ_API_KEY_1=...'
            Write-Info 'API keys in config.txt are ignored — use .env only.'
        }
        'NO_CONFIG' {
            Write-Warn 'config.txt not found. Default generated on first run.'
            Write-Info 'Free Groq key: https://console.groq.com'
            Write-Info 'Copy .env.example to .env and add keys there (not config.txt).'
        }
    }

    $secretStatus = Invoke-PythonHelper -PythonExe $script:VenvPython -Command 'config_secrets'
    if ($secretStatus -match '^KEYS_IN_CONFIG:') {
        Write-Warn 'API key(s) found in config.txt are ignored — move them to .env'
        Write-Info "Ignored keys: $($secretStatus.Substring(15))"
    }

    $orModStatus = Invoke-PythonHelper -PythonExe $script:VenvPython -Command 'openrouter'
    if ($orModStatus -eq 'OPENROUTER_READY') {
        Write-Ok 'OpenRouter module found and ready to use (key + model valid).'
    } elseif ($orModStatus -eq 'OPENROUTER_READY_RECOMMEND_GROQ') {
        Write-Ok 'OpenRouter module found and ready (paid / non-:free model).'
        Write-Warn 'Recommendation: enable Groq first (ENABLE_GROQ=yes + GROQ_API_KEY_1 in .env).'
        Write-Info 'Agetha will use free Groq, then auto-switch to OpenRouter when Groq runs out.'
    } elseif ($orModStatus -match '^OPENROUTER_OK_NOT_READY:') {
        Write-Ok 'OpenRouter module found (_OpenRouterClient in agetha.core.ai_engine).'
        Write-Warn "OpenRouter not ready to use: $($orModStatus.Substring(24))"
        Write-Info 'Pick a live model slug from https://openrouter.ai/models and set OPENROUTER_MODEL.'
    } elseif ($orModStatus -match '^OPENROUTER_MISSING:') {
        Write-Warn "OpenRouter module not found: $($orModStatus.Substring(19))"
    } else {
        Write-Warn "OpenRouter module check unexpected: $orModStatus"
    }
    Write-Host ''

    # [7/7] py_compile
    Write-Head '[7 / 7]  Python syntax (py_compile)'
    $modules = @(
        'main.py', 'medic_helper.py',
        'agetha\app_config.py', 'agetha\utils.py',
        'agetha\core\ai_engine.py', 'agetha\core\memory_system.py', 'agetha\core\memory_search.py', 'agetha\core\companion_stats.py',
        'agetha\core\rhythm.py', 'agetha\core\dreams.py',
        'agetha\core\emotion_engine.py', 'agetha\core\emotional_history.py', 'agetha\core\audit_log.py',
        'agetha\commands\command_guard.py', 'agetha\commands\command_handlers.py', 'agetha\commands\system_commands.py',
        'agetha\platform\screen_reader.py', 'agetha\platform\screen_monitoring.py', 'agetha\platform\window_control.py', 'agetha\platform\voice_input.py',
        'agetha\platform\ocr_backends\__init__.py', 'agetha\platform\ocr_backends\base.py', 'agetha\platform\ocr_backends\tesseract_backend.py', 'agetha\platform\ocr_backends\unlimited_ocr_backend.py',
        'agetha\platform\autostart.py', 'agetha\platform\win_integration.py',
        'agetha\features\tts_player.py', 'agetha\features\web_rag.py', 'agetha\features\tasks.py',
        'agetha\features\status_providers.py', 'agetha\features\tray_scaffold.py',
        'agetha\ui\dashboard.py', 'agetha\ui\w95_window.py', 'agetha\ui\glitch_overlay.py', 'agetha\ui\virus_trivia.py'
    )
    $compileFail = $false
    foreach ($mod in $modules) {
        & $script:VenvPython -m py_compile (Join-Path $Script:Root $mod) 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Syntax error in $mod"
            $compileFail = $true
        }
    }
    if ($compileFail) {
        Write-Fail 'Fix syntax errors above before launching.'
        Wait-Key
        exit 1
    }
    $featStatus = Invoke-PythonHelper -PythonExe $script:VenvPython -Command 'features'
    if ($featStatus -eq 'FEATURE_OK') {
        Write-Ok 'Phase 1-6 modules import cleanly (memory_search, companion_stats, rhythm, dreams, tasks, emotion_engine, emotional_history, audit_log, autostart, win_integration, status_providers, tray_scaffold, dashboard, tts_player, web_rag, glitch_overlay, virus_trivia, w95_window).'
    } elseif ($featStatus -match '^FEATURE_FAIL:') {
        Write-Warn "Extension module import issue: $($featStatus.Substring(12))"
    }
    $realismStatus = Invoke-PythonHelper -PythonExe $script:VenvPython -Command 'realism'
    if ($realismStatus -eq 'REALISM_OK') {
        Write-Ok 'Realism APIs ready (host-mood presence, session recap, OCR coding-assist guardrails).'
    } elseif ($realismStatus -match '^REALISM_FAIL:') {
        Write-Warn "Realism API issue: $($realismStatus.Substring(13))"
    } else {
        Write-Info "Realism check skipped or unexpected: $realismStatus"
    }
    $phase4Test = Join-Path $Script:Root 'tests\test_phase4_realism.py'
    if (Test-Path -LiteralPath $phase4Test) {
        Write-Ok 'tests\test_phase4_realism.py present (run: python tests/test_phase4_realism.py).'
    } else {
        Write-Info 'tests\test_phase4_realism.py not found - optional QA suite.'
    }
    Write-Ok "All $($modules.Count) modules compile cleanly."
    Write-Host ''
}

# --- Main ---
Clear-Host
Write-Host ''
Write-Head '+============================================================+'
Write-Head '|     AGETHA.EXE  |  Startup & Health Check                 |'
Write-Head '|     Overhaul Edition  v' + $script:AppVersion + '                                  |'
Write-Head '+============================================================+'
Write-Host ''

$coreFiles = @(
    'main.py', 'medic_helper.py', 'requirements.txt',
    'agetha\app_config.py', 'agetha\utils.py',
    'agetha\core\ai_engine.py', 'agetha\core\memory_system.py', 'agetha\core\memory_search.py', 'agetha\core\companion_stats.py',
    'agetha\core\rhythm.py', 'agetha\core\dreams.py',
    'agetha\core\emotion_engine.py', 'agetha\core\emotional_history.py', 'agetha\core\audit_log.py',
    'agetha\commands\command_guard.py', 'agetha\commands\command_handlers.py', 'agetha\commands\system_commands.py',
    'agetha\platform\screen_reader.py', 'agetha\platform\screen_monitoring.py', 'agetha\platform\window_control.py', 'agetha\platform\voice_input.py',
    'agetha\platform\ocr_backends\__init__.py', 'agetha\platform\ocr_backends\base.py', 'agetha\platform\ocr_backends\tesseract_backend.py', 'agetha\platform\ocr_backends\unlimited_ocr_backend.py',
    'agetha\platform\autostart.py', 'agetha\platform\win_integration.py',
    'agetha\features\tts_player.py', 'agetha\features\web_rag.py', 'agetha\features\tasks.py',
    'agetha\features\status_providers.py', 'agetha\features\tray_scaffold.py',
    'agetha\ui\dashboard.py', 'agetha\ui\w95_window.py', 'agetha\ui\glitch_overlay.py', 'agetha\ui\virus_trivia.py'
)
$missingCore = $coreFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $Script:Root $_)) }
if ($missingCore) {
    foreach ($f in $missingCore) { Write-Fail "Missing required file: $f" }
    Write-Info 'Place this script in the Agetha project root folder.'
    Wait-Key
    exit 1
}
Write-Ok 'Core project files confirmed (v5.5.1 modules + requirements.txt).'
Write-Host ''
Test-GitHubUpdate
New-AgethaDesktopShortcut
Write-Host ''

Write-Head '+----------------------------------------------------------+'
Write-Head '|  [A]  System Architecture Verification                    |'
Write-Head '+----------------------------------------------------------+'
Write-Host ''

if (Test-Arm64Host) {
    $script:RequireX64Python = $true
    Write-Warn 'Windows host architecture is ARM64 (this does not describe Python).'
    Write-Info 'Agetha will select an x64 Python process running under Prism.'
    Write-Info 'Python package compliance check required - see Section B.'
    Write-Host ''
    Test-Arm64Python
} else {
    Write-Ok 'x86-64 (AMD64) native host - no ARM64 constraints apply.'
    Write-Host ''
}

Invoke-StandardChecks

Write-Head '+============================================================+'
Write-Head '|  All checks complete.  Launching Agetha...                 |'
Write-Head '+============================================================+'
Write-Host ''

& $script:VenvPython (Join-Path $Script:Root 'main.py')
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
    Write-Host ''
    Write-Line "  [----]  Agetha exited with code: $exitCode" 'Red'
    Write-Info 'Scroll up in this window to read the crash details.'
    Write-Host ''
    Wait-Key
}
exit $exitCode
