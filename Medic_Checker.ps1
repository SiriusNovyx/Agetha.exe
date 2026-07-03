#Requires -Version 5.1
<#
.SYNOPSIS
  Agetha startup health check and launcher (Overhaul Edition v3.5.5+)

.DESCRIPTION
  Verifies project files, ARM64/x64 Python compatibility, venv, packages,
  optional Tesseract, assets, config, and py_compile - then launches main.py.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

$Script:Root = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
Set-Location -LiteralPath $Script:Root

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
    $v = Get-ConfigValue -Key 'APP_VERSION' -Default '3.5.1'
    if ($v) { return $v }
    return '3.5.1'
}

function Write-Line([string]$Text, [ConsoleColor]$Color = 'Gray') {
    Write-Host $Text -ForegroundColor $Color
}

function Write-Ok([string]$Text)   { Write-Line "  [ OK ]  $Text" 'Green' }
function Write-Warn([string]$Text) { Write-Line "  [WARN]  $Text" 'Yellow' }
function Write-Fail([string]$Text) { Write-Line "  [FAIL]  $Text" 'Red' }
function Write-Info([string]$Text) { Write-Line "  [    ]  $Text" 'DarkGray' }
function Write-Step([string]$Text) { Write-Line "  $Text" 'Cyan' }
function Write-Head([string]$Text) { Write-Line $Text 'White' }

try {
    $script:AppVersion = Get-AppVersion
    $Host.UI.RawUI.WindowTitle = "Agetha.exe  -  Health Check  |  v$script:AppVersion"
} catch {
    $script:AppVersion = '3.5.0'
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
        $desktop = [Environment]::GetFolderPath('Desktop')
        $lnk = Join-Path $desktop 'Agetha.lnk'
        $target = Join-Path $Script:Root 'Medic_Checker.bat'
        $shell = New-Object -ComObject WScript.Shell
        $sc = $shell.CreateShortcut($lnk)
        $sc.TargetPath = $target
        $sc.WorkingDirectory = $Script:Root
        $sc.Description = 'Agetha AI Companion'
        $icon = Join-Path $Script:Root 'assets\icon.ico'
        if (Test-Path -LiteralPath $icon) { $sc.IconLocation = $icon }
        $sc.Save()
        Write-Ok "Desktop shortcut: $lnk"
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

function Get-PythonMachine {
    param([string]$PythonExe)
    try {
        $out = & $PythonExe -c 'import platform; print(platform.machine())' 2>$null
        if ($LASTEXITCODE -eq 0) { return ($out | Select-Object -Last 1).ToString().Trim() }
    } catch { }
    return Invoke-PythonHelper -PythonExe $PythonExe -Command 'platform'
}

function Resolve-PythonCommand {
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

function Find-X64PythonPath {
    $candidates = @(
        Get-ChildItem -Path "$env:LOCALAPPDATA\Programs\Python\Python31*" -Directory -ErrorAction SilentlyContinue
        Get-ChildItem -Path 'C:\Python31*' -Directory -ErrorAction SilentlyContinue
    ) | Where-Object { Test-Path (Join-Path $_.FullName 'python.exe') }

    foreach ($dir in $candidates) {
        $arch = Get-PythonMachine -PythonExe (Join-Path $dir.FullName 'python.exe')
        if ($arch -eq 'AMD64') { return $dir.FullName }
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

    $arch = Get-PythonMachine -PythonExe 'python'
    if (-not $arch) {
        Write-Warn 'Python still not found after PATH refresh.'
        Write-Info 'Close this window, reopen it, and run this script again.'
        Wait-Key
        exit 1
    }
    if ($arch -ne 'AMD64') {
        Write-Warn "Python found but reports arch '$arch' - expected AMD64."
        Write-Info 'Close this window and re-run to start with a clean PATH.'
        Wait-Key
        exit 1
    }
    Write-Ok "[D4/4]  PATH refreshed - Python $arch (x64) is now active."

    $venvPy = Join-Path $Script:Root 'venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPy) {
        $venvArch = Get-PythonMachine -PythonExe $venvPy
        if ($venvArch -and $venvArch -ne 'AMD64') {
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
    Write-Info 'Agetha depends on: pygame, pyautogui, mss, pillow'
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

function Test-Arm64Python {
    Write-Head '+----------------------------------------------------------+'
    Write-Head '|  [B]  Python Variant Evaluation  (ARM64 host)             |'
    Write-Head '+----------------------------------------------------------+'
    Write-Host ''

    $pyArch = 'MISSING'
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $detected = Get-PythonMachine -PythonExe 'python'
        if ($detected) {
            $pyArch = $detected
            Write-Info "Python found on PATH - compiled architecture: $pyArch"
        }
    } else {
        Write-Info 'No Python binary found on PATH.'
    }

    if ($pyArch -eq 'AMD64') {
        Write-Ok 'Python is x64 (AMD64) - running under Prism emulation.'
        Write-Ok 'Binary wheels for pygame / pyautogui / mss install correctly.'
        Write-Host ''
        return
    }

    if ($pyArch -eq 'ARM64') {
        Write-Warn 'Native ARM64 Python detected - incompatible with PyPI wheels.'
    } else {
        Write-Warn 'No compatible Python found on PATH.'
    }

    Write-Step 'Scanning known install directories for an existing x64 build...'
    Write-Host ''
    $x64Path = Find-X64PythonPath
    if ($x64Path) {
        Write-Ok "Found existing x64 Python at: $x64Path"
        Write-Step 'Prepending to session PATH - no download required.'
        $env:PATH = "$x64Path;$x64Path\Scripts;$env:PATH"
        Write-Ok 'x64 Python is now active for this session.'
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
        if ($envStatus -eq 'SET') { return 'SET' }
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
        Write-Fail 'Python not found on PATH.'
        Write-Info 'https://www.python.org/downloads/'
        Wait-Key
        exit 1
    }
    $ver = Invoke-Python $script:PythonCmd --version 2>&1 | Out-String
    Write-Ok $ver.Trim()
    Write-Host ''

    # [2/7] venv
    Write-Head '[2 / 7]  Virtual environment'
    $venvDir = Join-Path $Script:Root 'venv'
    $venvPy = Join-Path $venvDir 'Scripts\python.exe'
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
    $required = @('pillow', 'pyautogui', 'pytesseract', 'numpy', 'pygame', 'requests', 'groq', 'mss', 'psutil')
    $missing = @()
    foreach ($pkg in $required) {
        $null = & $script:VenvPython -m pip show $pkg 2>&1
        if ($LASTEXITCODE -ne 0) { $missing += $pkg }
    }
    if ($missing.Count -eq 0) {
        Write-Ok 'All required packages installed.'
    } elseif (-not $script:AutoPipInstall) {
        Write-Warn "Missing: $($missing -join ', ')"
        Write-Warn 'AUTO_PIP_INSTALL=no - run: pip install -r requirements.txt'
    } else {
        Write-Step "Missing: $($missing -join ', ')"
        Write-Step 'Installing from requirements.txt - please wait...'
        & $script:VenvPython -m pip install -r (Join-Path $Script:Root 'requirements.txt') --quiet --disable-pip-version-check
        if ($LASTEXITCODE -ne 0) {
            Write-Fail 'Package install failed. Run: pip install -r requirements.txt'
            Wait-Key
            exit 1
        }
        Write-Ok 'Packages installed.'
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
    $needsTts = $voiceOutputMode -in @('tts_only', 'both')
    $optionalPkgs = @()
    if ($enableVoice) {
        $optionalPkgs += 'SpeechRecognition', 'PyAudio'
        if ($useLocalStt) { $optionalPkgs += 'faster-whisper' }
    }
    if ($enableDnd) { $optionalPkgs += 'tkinterdnd2' }
    if ($needsTts) { $optionalPkgs += 'pyttsx3' }
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
                Write-Info 'TTS: VOICE_OUTPUT_MODE=tts_only|both needs pyttsx3'
            }
        } else {
            Write-Warn "Optional missing: $($optMissing -join ', ')"
            Write-Info 'Set AUTO_PIP_INSTALL=yes or: pip install -r requirements.txt'
        }
    }
    if ($needsTts) {
        $ttsStatus = Invoke-PythonHelper -PythonExe $script:VenvPython -Command 'tts'
        if ($ttsStatus -eq 'TTS_OK') {
            Write-Ok "TTS (pyttsx3) ready for VOICE_OUTPUT_MODE=$voiceOutputMode."
        } elseif ($ttsStatus -eq 'TTS_MISSING') {
            Write-Warn "VOICE_OUTPUT_MODE=$voiceOutputMode but pyttsx3 not installed - falls back to bleeps."
            Write-Info 'Install: pip install "pyttsx3>=2.90,<3.0.0"'
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
        'thinking-static.gif', 'thinking.gif', 'barrio.ttf'
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
        Write-Ok 'All 20 assets present.'
    } else {
        Write-Warn 'Missing assets will cause broken or invisible animations.'
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
        Write-Ok 'ENABLE_LONGTERM_MEMORY=yes - search_memory + JSONL dual-write active.'
    } else {
        Write-Info 'ENABLE_LONGTERM_MEMORY=no - long-term search disabled.'
    }
    Write-Info "VOICE_OUTPUT_MODE=$(Get-ConfigValue -Key 'VOICE_OUTPUT_MODE' -Default 'bleeps_only')"
    $enableWebRag = (Get-ConfigValue -Key 'ENABLE_WEB_RAG' -Default 'no') -match '^(?i)yes$'
    if ($enableWebRag) {
        Write-Ok 'ENABLE_WEB_RAG=yes - search_web / fetch_webpage active (CAUTION confirmations).'
    } else {
        Write-Info 'ENABLE_WEB_RAG=no - web search/fetch disabled (default).'
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
            if (Test-Path -LiteralPath (Join-Path $Script:Root '.env')) {
                Write-Ok '.env - Groq API key configured.'
            } else {
                Write-Ok 'config.txt - Groq API key configured.'
            }
        }
        'LOCAL'       { Write-Ok 'config.txt - Local AI (Ollama) mode active.' }
        'OPENROUTER'  { Write-Ok 'config.txt - OpenRouter mode active.' }
        'LOCAL_NO_MODEL' {
            Write-Warn 'USE_LOCAL_AI=yes but LOCAL_AI_MODEL is blank.'
            Write-Info 'Run: ollama list  then set LOCAL_AI_MODEL in config.txt'
        }
        'EMPTY' {
            Write-Warn 'No API key in config.txt or .env - Agetha will not respond.'
            Write-Info 'Free key: https://console.groq.com'
            Write-Info 'Recommended: copy .env.example to .env and add GROQ_API_KEY_1=...'
        }
        'NO_CONFIG' {
            Write-Warn 'config.txt not found. Default generated on first run.'
            Write-Info 'Free Groq key: https://console.groq.com'
            Write-Info 'Or copy .env.example to .env and add keys there.'
        }
    }
    Write-Host ''

    # [7/7] py_compile
    Write-Head '[7 / 7]  Python syntax (py_compile)'
    $modules = @(
        'main.py', 'medic_helper.py',
        'agetha\app_config.py', 'agetha\utils.py',
        'agetha\core\ai_engine.py', 'agetha\core\memory_system.py', 'agetha\core\memory_search.py', 'agetha\core\companion_stats.py',
        'agetha\commands\command_guard.py', 'agetha\commands\command_handlers.py', 'agetha\commands\system_commands.py',
        'agetha\platform\screen_reader.py', 'agetha\platform\window_control.py', 'agetha\platform\voice_input.py',
        'agetha\features\tts_player.py', 'agetha\features\web_rag.py',
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
        Write-Ok 'Phase 1-4 modules import cleanly (memory_search, companion_stats, dashboard, tts_player, web_rag, glitch_overlay, virus_trivia, w95_window).'
    } elseif ($featStatus -match '^FEATURE_FAIL:') {
        Write-Warn "Extension module import issue: $($featStatus.Substring(12))"
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
    'agetha\commands\command_guard.py', 'agetha\commands\command_handlers.py', 'agetha\commands\system_commands.py',
    'agetha\platform\screen_reader.py', 'agetha\platform\window_control.py', 'agetha\platform\voice_input.py',
    'agetha\features\tts_player.py', 'agetha\features\web_rag.py',
    'agetha\ui\dashboard.py', 'agetha\ui\w95_window.py', 'agetha\ui\glitch_overlay.py', 'agetha\ui\virus_trivia.py'
)
$missingCore = $coreFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $Script:Root $_)) }
if ($missingCore) {
    foreach ($f in $missingCore) { Write-Fail "Missing required file: $f" }
    Write-Info 'Place this script in the Agetha project root folder.'
    Wait-Key
    exit 1
}
Write-Ok 'Core project files confirmed (17 modules + requirements.txt).'
Write-Host ''
Test-GitHubUpdate
New-AgethaDesktopShortcut
Write-Host ''

Write-Head '+----------------------------------------------------------+'
Write-Head '|  [A]  System Architecture Verification                    |'
Write-Head '+----------------------------------------------------------+'
Write-Host ''

if (Test-Arm64Host) {
    Write-Warn 'ARM64 host confirmed.  Prism x64-emulation layer is active.'
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
