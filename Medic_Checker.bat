@echo off
setlocal EnableDelayedExpansion
title Agetha.exe  -  Health Check  ^|  Medic_Checker  v2.0
chcp 65001 >nul 2>&1
cls

:: ════════════════════════════════════════════════════════════════════════
::  AGETHA.EXE  |  STARTUP & HEALTH CHECK  |  ARM64-AWARE EDITION  v2.0
::
::  PURPOSE
::    Enforces the x64 (AMD64) Python variant on ARM64 Snapdragon platforms
::    (Qualcomm X Elite, X Plus, etc.) where native ARM64 Python builds fail
::    to install binary-wheel packages such as pygame and pyautogui because
::    no ARM64 wheels exist on PyPI and compiling from source requires MSVC.
::
::  SECTION MAP
::    [0]  ANSI colour definitions
::    [A]  System architecture verification  (three independent methods)
::    [B]  Python variant evaluation          (ARM64 hosts only)
::    [C]  Conditional intervention & repair offer
::    [D]  Automated download + silent install + PATH refresh pipeline
::    [1-6] Standard health checks  (Python, venv, packages, Tesseract, assets, config)
::    [*]  Launch
:: ════════════════════════════════════════════════════════════════════════


:: ════════════════════════════════════════════════════════════════════════
::  [0]  ANSI COLOUR SETUP
::
::  Windows 11 - required on all ARM64 Snapdragon machines - always supports
::  ANSI Virtual Terminal Processing. We capture the ESC character (ASCII 27)
::  via a lightweight PowerShell call and build named colour prefix variables.
::  Every echo line uses one of these prefixes followed by !RST! at the end
::  to avoid colour bleed into the next line.
::
::  Colour semantics used throughout this script:
::    GRN - success, good state, confirmed OK
::    YLW - warnings, non-critical issues, cautions
::    RED - failures, errors, critical blockers
::    CYN - informational actions, status updates
::    WHT - section headers, structural labels
::    DIM - secondary/supplementary explanatory text
::    BLU - user-facing prompts requiring input
::    BLD - bold intensity modifier (combine with any colour)
::    RST - resets ALL attributes; always append to every coloured echo
:: ════════════════════════════════════════════════════════════════════════
for /f %%Z in ('powershell -nologo -noprofile -command "[char]27"') do set "ESC=%%Z"

set "GRN=!ESC![92m"
set "YLW=!ESC![93m"
set "RED=!ESC![91m"
set "CYN=!ESC![96m"
set "WHT=!ESC![97m"
set "DIM=!ESC![90m"
set "BLU=!ESC![94m"
set "BLD=!ESC![1m"
set "RST=!ESC![0m"


:: ════════════════════════════════════════════════════════════════════════
::  HEADER BANNER
:: ════════════════════════════════════════════════════════════════════════
echo.
echo !WHT!!BLD!  ╔════════════════════════════════════════════════════════════╗!RST!
echo !WHT!!BLD!  ║     AGETHA.EXE  ^|  Startup ^& Health Check                 ║!RST!
echo !WHT!!BLD!  ║     ARM64-Aware Edition  v2.0                              ║!RST!
echo !WHT!!BLD!  ╚════════════════════════════════════════════════════════════╝!RST!
echo.


:: ════════════════════════════════════════════════════════════════════════
::  PRE-FLIGHT: Verify the working directory contains main.py
::  Everything downstream depends on this folder being correct.
:: ════════════════════════════════════════════════════════════════════════
if not exist "main.py" (
    echo !RED!  [FAIL]  main.py not found in this folder.!RST!
    echo !DIM!  [    ]  Place this script in the same directory as main.py.!RST!
    echo.
    pause
    exit /b 1
)
echo !GRN!  [ OK ]  Working directory confirmed  ^(main.py present^).!RST!
echo.


:: ════════════════════════════════════════════════════════════════════════
::  [A]  SYSTEM ARCHITECTURE VERIFICATION
::
::  Determine whether the host OS is running on native ARM64 silicon -
::  i.e. a Snapdragon X Elite / Plus, Qualcomm QCM, or similar - rather
::  than on classic x86-64 hardware. This is the TRUE chip architecture,
::  NOT the bitness of the currently running cmd.exe process.
::
::  Three independent detection methods are OR-ed together so no emulation
::  layer or unusual process context can produce a false negative:
::
::  METHOD 1 - PROCESSOR_ARCHITECTURE environment variable
::    Set by Windows to reflect the current PROCESS architecture.
::    A native ARM64 cmd.exe process will see ARM64 here directly.
::
::  METHOD 2 - PROCESSOR_ARCHITEW6432 environment variable
::    This "hidden" WoW64 variable is populated whenever a lower-bitness
::    process runs on a higher-bitness OS. On ARM64 Windows, an x64-
::    emulated cmd.exe (running under Prism) sees PROCESSOR_ARCHITECTURE
::    as AMD64 but PROCESSOR_ARCHITEW6432 as ARM64. Catching this second
::    variable prevents missed detection in emulated-process contexts.
::
::  METHOD 3 - PowerShell RuntimeInformation.OSArchitecture  (definitive)
::    This .NET System.Runtime property always returns the TRUE underlying
::    hardware OS architecture regardless of the calling process's own
::    bitness. It is the definitive tiebreaker and catches every remaining
::    edge case, including deeply nested emulation layers.
:: ════════════════════════════════════════════════════════════════════════
echo !WHT!!BLD!  ┌──────────────────────────────────────────────────────────┐!RST!
echo !WHT!!BLD!  │  [A]  System Architecture Verification                    │!RST!
echo !WHT!!BLD!  └──────────────────────────────────────────────────────────┘!RST!
echo.

set "IS_ARM64=0"

:: ── Method 1: PROCESSOR_ARCHITECTURE ─────────────────────────────────
:: Fast env-var probe. Reliable only when cmd.exe itself is a native ARM64
:: process. Silent on x64-emulated processes where it reads AMD64 instead.
if /I "!PROCESSOR_ARCHITECTURE!"=="ARM64" (
    set "IS_ARM64=1"
    echo !DIM!  [    ]  Method 1: PROCESSOR_ARCHITECTURE = ARM64!RST!
    echo !DIM!  [    ]          → cmd.exe is running as a native ARM64 process.!RST!
)

:: ── Method 2: PROCESSOR_ARCHITEW6432 ─────────────────────────────────
:: The WoW64 companion variable. Populated when a 32-bit or x64-emulated
:: process runs on a 64-bit OS via the Windows-on-Windows compatibility
:: layer (WoW64) or the ARM64 Prism emulation engine. On ARM64 Windows,
:: this will read ARM64 for any non-native (x64 or x86) process.
if /I "!PROCESSOR_ARCHITEW6432!"=="ARM64" (
    set "IS_ARM64=1"
    echo !DIM!  [    ]  Method 2: PROCESSOR_ARCHITEW6432 = ARM64!RST!
    echo !DIM!  [    ]          → cmd.exe is an x64-emulated process on ARM64 host.!RST!
)

:: ── Method 3: PowerShell OSArchitecture (only if methods 1+2 were silent) ─
:: Invoke PowerShell's RuntimeInformation.OSArchitecture. This property
:: inspects the host OS CPU registration directly and is immune to process-
:: level architecture masking. It is the gold-standard tiebreaker.
if "!IS_ARM64!"=="0" (
    echo !DIM!  [    ]  Methods 1^&2 inconclusive - running PSI definitive check...!RST!
    powershell -nologo -noprofile -command "[System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -eq [System.Runtime.InteropServices.Architecture]::Arm64" > "%TEMP%\agetha_arch.tmp" 2>nul
    for /f "usebackq tokens=*" %%R in ("%TEMP%\agetha_arch.tmp") do (
        if /I "%%R"=="True" (
            set "IS_ARM64=1"
            echo !DIM!  [    ]  Method 3: OSArchitecture = Arm64  ^(PSI definitive result^)!RST!
        )
    )
    del "%TEMP%\agetha_arch.tmp" >nul 2>&1
)

:: ── Verdict ───────────────────────────────────────────────────────────
if "!IS_ARM64!"=="1" (
    echo !YLW!  [ARCH]  ARM64 host confirmed.  Prism x64-emulation layer is active.!RST!
    echo !DIM!  [    ]  Python package compliance check required - see Section B.!RST!
    echo.
    goto :CHECK_PYTHON_ARCH
) else (
    echo !GRN!  [ OK ]  x86-64 ^(AMD64^) native host - no ARM64 constraints apply.!RST!
    echo.
    goto :STANDARD_CHECKS
)


:: ════════════════════════════════════════════════════════════════════════
::  [B]  PYTHON VARIANT EVALUATION  (ARM64 hosts only)
::
::  We have confirmed this is an ARM64 machine. Now determine which Python
::  binary is active and whether it is the x64 variant required for package
::  compatibility. There are three possible states:
::
::    AMD64 (x64) - Python was compiled for x86-64 and runs transparently
::                  under the Prism emulation layer. All PyPI binary wheels
::                  for Windows x64 install without errors.    ✔  PASS
::
::    ARM64 (native) - Python was compiled natively for ARM64.
::                  PyPI has no ARM64 wheels for pygame, pyautogui, mss,
::                  etc. pip would attempt to compile C extensions from
::                  source and immediately fail due to missing MSVC tooling.
::                  This is the critical configuration hazard.  ✗  FAIL
::
::    MISSING - No Python installation was found on PATH at all.  ✗  FAIL
::
::  Detection uses platform.machine() rather than sys.maxsize because
::  sys.maxsize equals 2^63-1 for BOTH AMD64 and ARM64 on 64-bit Windows
::  (both are 64-bit pointer platforms). platform.machine() returns the
::  compiler target string "AMD64" or "ARM64" directly from the C runtime.
:: ════════════════════════════════════════════════════════════════════════
:CHECK_PYTHON_ARCH
echo !WHT!!BLD!  ┌──────────────────────────────────────────────────────────┐!RST!
echo !WHT!!BLD!  │  [B]  Python Variant Evaluation  (ARM64 host)             │!RST!
echo !WHT!!BLD!  └──────────────────────────────────────────────────────────┘!RST!
echo.

set "PY_ARCH=MISSING"

:: Probe whatever Python is first on PATH
python --version >nul 2>&1
if !errorlevel! EQU 0 (
    for /f "usebackq delims=" %%A in (`python -c "import platform; print(platform.machine())"`) do set "PY_ARCH=%%A"
    echo !DIM!  [    ]  Python found on PATH - compiled architecture: !PY_ARCH!!RST!
) else (
    echo !DIM!  [    ]  No Python binary found on PATH.!RST!
)

:: ── PASS: x64 Python under Prism ─────────────────────────────────────
if /I "!PY_ARCH!"=="AMD64" (
    echo !GRN!  [ OK ]  Python is x64 ^(AMD64^) - running under Prism emulation.!RST!
    echo !GRN!  [ OK ]  Binary wheels for pygame / pyautogui / mss install correctly.!RST!
    echo.
    goto :STANDARD_CHECKS
)

:: ── FAIL: ARM64 or MISSING - scan known install paths first ──────────
:: Before offering a full download, search common installation directories
:: for an x64 Python that is installed but simply not on PATH. If found,
:: we can just prepend it to the session PATH and skip the download entirely.
if /I "!PY_ARCH!"=="ARM64" (
    echo !YLW!  [WARN]  Native ARM64 Python detected - incompatible with PyPI wheels.!RST!
) else (
    echo !YLW!  [WARN]  No Python found on PATH.!RST!
)

echo !CYN!  [    ]  Scanning known install directories for an existing x64 build...!RST!
echo.

set "X64_PY_PATH="

:: Scan per-user AppData installation (default for user-scoped installs)
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python31*") do (
    if exist "%%D\python.exe" (
        for /f "usebackq delims=" %%A in (`"%%D\python.exe" -c "import platform; print(platform.machine())" 2^>nul`) do (
            if /I "%%A"=="AMD64" if not defined X64_PY_PATH set "X64_PY_PATH=%%D"
        )
    )
)

:: Scan legacy C:\Python3xx system-wide path (less common but possible)
if not defined X64_PY_PATH (
    for /d %%D in ("C:\Python31*") do (
        if exist "%%D\python.exe" (
            for /f "usebackq delims=" %%A in (`"%%D\python.exe" -c "import platform; print(platform.machine())" 2^>nul`) do (
                if /I "%%A"=="AMD64" if not defined X64_PY_PATH set "X64_PY_PATH=%%D"
            )
        )
    )
)

:: x64 Python found somewhere on disk but not on PATH - use it immediately
if defined X64_PY_PATH (
    echo !GRN!  [ OK ]  Found existing x64 Python at: !X64_PY_PATH!!RST!
    echo !CYN!  [    ]  Prepending to session PATH - no download required.!RST!
    set "PATH=!X64_PY_PATH!;!X64_PY_PATH!\Scripts;!PATH!"
    echo !GRN!  [ OK ]  x64 Python is now active for this session.!RST!
    echo.
    goto :STANDARD_CHECKS
)

echo !YLW!  [EVAL]  No x64 Python installation found anywhere on this system.!RST!
goto :OFFER_REPAIR


:: ════════════════════════════════════════════════════════════════════════
::  [C]  CONDITIONAL INTERVENTION & REPAIR OFFER
::
::  Reaching this label confirms:
::    (1) The host is native ARM64 silicon, AND
::    (2) No x64 Python installation exists on PATH or in known directories.
::
::  We display a full, plain-language explanation of WHY x64 Python is
::  required, along with exact details of what will be installed. NOTHING
::  is downloaded or changed until the user enters an explicit Y confirmation.
:: ════════════════════════════════════════════════════════════════════════
:OFFER_REPAIR
echo !RED!!BLD!  ╔════════════════════════════════════════════════════════════╗!RST!
echo !RED!!BLD!  ║   CRITICAL CONFIGURATION HAZARD - ACTION REQUIRED          ║!RST!
echo !RED!!BLD!  ╚════════════════════════════════════════════════════════════╝!RST!
echo.
echo !YLW!  Diagnosis:  ARM64 host - no compatible x64 Python found.!RST!
echo !DIM!  Detected Python architecture: !PY_ARCH!!RST!
echo.
echo !WHT!  Why x64 Python is required on Snapdragon / ARM64 systems:!RST!
echo.
echo !DIM!  ┌──────────────────────────────────────────────────────────────┐!RST!
echo !DIM!  │  Agetha depends on:  pygame  pyautogui  mss  pillow          │!RST!
echo !DIM!  │                                                                │!RST!
echo !DIM!  │  These packages ship as pre-built binary wheels (.whl) for   │!RST!
echo !DIM!  │  Windows x64 ONLY. No ARM64-native wheels exist on PyPI.     │!RST!
echo !DIM!  │                                                                │!RST!
echo !DIM!  │  With ARM64 Python, pip would try to compile C extensions     │!RST!
echo !DIM!  │  from source - and immediately fail because the required      │!RST!
echo !DIM!  │  MSVC / Cython build tools are absent.                        │!RST!
echo !DIM!  │                                                                │!RST!
echo !DIM!  │  x64 Python runs through Windows Prism (the ARM64-to-x64      │!RST!
echo !DIM!  │  binary translation engine) transparently and with no         │!RST!
echo !DIM!  │  measurable performance impact for Agetha's workload.         │!RST!
echo !DIM!  └──────────────────────────────────────────────────────────────┘!RST!
echo.

:: ── Configure the download target ────────────────────────────────────
:: MAINTENANCE NOTE: Update PY_VER when a new Python 3.x stable release
:: is published. Current releases: https://www.python.org/downloads/windows/
:: Download the "Windows installer (64-bit)" link - that is the AMD64 build.
set "PY_VER=3.13.3"
set "PY_INSTALLER_NAME=python-!PY_VER!-amd64.exe"
set "PY_URL=https://www.python.org/ftp/python/!PY_VER!/!PY_INSTALLER_NAME!"
set "PY_INSTALLER=%TEMP%\!PY_INSTALLER_NAME!"

echo !CYN!  Proposed remedy - Python x64 silent installation:!RST!
echo.
echo !DIM!    Version     : Python !PY_VER! (x64 / AMD64 for Windows)!RST!
echo !DIM!    Source      : https://www.python.org  ^(official FTP mirror^)!RST!
echo !DIM!    Scope       : Current user only  ^(no UAC / admin required^)!RST!
echo !DIM!    PATH        : Prepended automatically  ^(PrependPath=1^)!RST!
echo !DIM!    Launcher    : py.exe version selector included!RST!
echo !DIM!    Temp file   : !PY_INSTALLER!!RST!
echo.

:: Explicit Y/N gate - no system changes until confirmed
echo !BLU!!BLD!  Would you like to automatically download and install!RST!
echo !BLU!!BLD!  Python !PY_VER! x64 ^(AMD64^) now?!RST!
echo.
echo !DIM!    [Y]  Yes - proceed with automated download and install!RST!
echo !DIM!    [N]  No  - exit and consult manual install instructions!RST!
echo.
set "CHOICE="
set /p "CHOICE=  Your choice [Y/N]: "
echo.

if /I "!CHOICE!"=="Y"   goto :INSTALL_PYTHON
if /I "!CHOICE!"=="YES" goto :INSTALL_PYTHON

:: User declined - provide manual guidance and exit cleanly
echo !RED!  [EXIT]  Automated installation declined.!RST!
echo !DIM!  [    ]  Agetha cannot start without a compatible Python.!RST!
echo.
echo !DIM!  Manual installation steps:!RST!
echo !DIM!    1. Open  https://www.python.org/downloads/windows/!RST!
echo !DIM!    2. Click "Windows installer ^(64-bit^)" for Python 3.13.x!RST!
echo !DIM!    3. Run the installer - tick "Add Python to PATH"!RST!
echo !DIM!    4. Close this window and re-run this script.!RST!
echo.
pause
exit /b 1


:: ════════════════════════════════════════════════════════════════════════
::  [D]  AUTOMATED INSTALLATION PIPELINE
::
::  Executed only after explicit user confirmation (Y above).
::  Four sequential stages - each must succeed before the next begins.
::
::  D1 - DOWNLOAD
::    Uses PowerShell Invoke-WebRequest written to a temp .ps1 file to
::    avoid batch/PowerShell quoting conflicts. $ProgressPreference is
::    set to SilentlyContinue to suppress the default PowerShell download
::    progress bar which reduces throughput 3-5× in PowerShell 5.1.
::    UseBasicParsing is specified to bypass the IE rendering engine.
::
::  D2 - VERIFY
::    Confirms the downloaded file exists and its size is plausible.
::    Python 3.13 x64 installer is ~25-27 MB. Anything under 20 MB
::    is treated as a corrupt/partial download and rejected immediately.
::
::  D3 - SILENT INSTALL
::    Runs the official Python installer with:
::      /quiet         - zero UI, no progress window, no UAC prompt
::      PrependPath=1  - adds Python to the FRONT of the system PATH
::      InstallAllUsers=0 - current user scope, no elevation required
::      Include_launcher=1 - installs the py.exe version-selector tool
::    start /wait blocks until the installer process exits and propagates
::    its exit code so we can detect installation failures.
::
::  D4 - PATH REFRESH + STALE VENV HANDLING
::    The running cmd.exe session inherited its PATH at launch time before
::    the installer ran. We query the UPDATED PATH from the Windows registry
::    via PowerShell GetEnvironmentVariable and overwrite our in-session
::    PATH so subsequent commands immediately find the new python.exe.
::    Additionally, if a venv directory exists and was built with the wrong
::    (ARM64) Python, it is removed so step [2/6] recreates it correctly.
:: ════════════════════════════════════════════════════════════════════════
:INSTALL_PYTHON
echo !WHT!!BLD!  ┌──────────────────────────────────────────────────────────┐!RST!
echo !WHT!!BLD!  │  [D]  Automated Python x64 Installation Pipeline          │!RST!
echo !WHT!!BLD!  └──────────────────────────────────────────────────────────┘!RST!
echo.


:: ── D1 : Download ────────────────────────────────────────────────────
echo !CYN!  [D1/4]  Downloading Python !PY_VER! x64 from python.org...!RST!
echo !DIM!  [    ]  !PY_URL!!RST!
echo !DIM!  [    ]  This may take 30–90 seconds depending on your connection.!RST!
echo.

:: Write the download logic to a temp PS1 file to avoid embedding complex
:: PowerShell syntax inside a batch -command string (quoting nightmare).
set "DL_SCRIPT=%TEMP%\agetha_dl.ps1"
(echo $ProgressPreference = 'SilentlyContinue') > "!DL_SCRIPT!"
(echo try {) >> "!DL_SCRIPT!"
(echo     Invoke-WebRequest -Uri '!PY_URL!' -OutFile '!PY_INSTALLER!' -UseBasicParsing) >> "!DL_SCRIPT!"
(echo     Write-Output 'OK') >> "!DL_SCRIPT!"
(echo } catch {) >> "!DL_SCRIPT!"
(echo     Write-Output "FAIL: $($_.Exception.Message)") >> "!DL_SCRIPT!"
(echo }) >> "!DL_SCRIPT!"

:: Execute the download script and capture its single-line status output
powershell -nologo -noprofile -ExecutionPolicy Bypass -File "!DL_SCRIPT!" > "%TEMP%\agetha_dlstatus.tmp" 2>nul
del "!DL_SCRIPT!" >nul 2>&1

:: Read the first non-empty output token - will be "OK" or "FAIL:"
set "DL_STATUS=FAIL"
set "DL_GOT="
for /f "usebackq tokens=1" %%S in ("%TEMP%\agetha_dlstatus.tmp") do (
    if not defined DL_GOT set "DL_STATUS=%%S" & set "DL_GOT=1"
)
del "%TEMP%\agetha_dlstatus.tmp" >nul 2>&1

if not "!DL_STATUS!"=="OK" (
    echo !RED!  [FAIL]  Download failed.  Status: !DL_STATUS!!RST!
    echo !DIM!  [    ]  Check your internet connection and try again.!RST!
    echo !DIM!  [    ]  Manual URL: !PY_URL!!RST!
    pause
    exit /b 1
)
echo !GRN!  [D1/4]  Download complete.!RST!
echo.


:: ── D2 : Verify ──────────────────────────────────────────────────────
echo !CYN!  [D2/4]  Verifying downloaded installer...!RST!

if not exist "!PY_INSTALLER!" (
    echo !RED!  [FAIL]  Installer file not found after download.  Aborting.!RST!
    pause
    exit /b 1
)

:: Confirm file size is plausible - Python 3.13 x64 installer is ~25-27 MB.
:: Anything under 20 MB is a truncated or corrupted download.
for %%F in ("!PY_INSTALLER!") do set "FILE_BYTES=%%~zF"
if !FILE_BYTES! LSS 20000000 (
    echo !RED!  [FAIL]  Installer appears corrupt  ^(!FILE_BYTES! bytes, expected ~26 MB^).!RST!
    echo !DIM!  [    ]  Removing partial file. Please retry.!RST!
    del "!PY_INSTALLER!" >nul 2>&1
    pause
    exit /b 1
)
echo !GRN!  [D2/4]  File verified  -  size: !FILE_BYTES! bytes.!RST!
echo.


:: ── D3 : Silent installation ─────────────────────────────────────────
echo !CYN!  [D3/4]  Running Python installer silently - please wait...!RST!
echo !DIM!  [    ]  Flags:  /quiet  PrependPath=1  InstallAllUsers=0  Include_launcher=1!RST!
echo !DIM!          /quiet          - no UI window, no progress bar, no UAC prompt!RST!
echo !DIM!          PrependPath=1   - adds python.exe to the FRONT of PATH!RST!
echo !DIM!          InstallAllUsers=0 - current user scope, no admin elevation needed!RST!
echo !DIM!          Include_launcher  - installs py.exe version-selector tool!RST!
echo.

:: start /wait runs the installer synchronously and returns its exit code.
:: The /quiet flag suppresses all UI. Installer runs for ~30-60 seconds.
start /wait "" "!PY_INSTALLER!" /quiet PrependPath=1 InstallAllUsers=0 Include_launcher=1

set "INST_CODE=!errorlevel!"
del "!PY_INSTALLER!" >nul 2>&1   & :: Remove installer binary from TEMP immediately

if !INST_CODE! NEQ 0 (
    echo !RED!  [FAIL]  Installer exited with code !INST_CODE!.!RST!
    echo !DIM!  [    ]  Common causes: insufficient disk space, AV software quarantine,!RST!
    echo !DIM!  [    ]  or a corrupted prior Python install in Add/Remove Programs.!RST!
    echo !DIM!  [    ]  Try uninstalling existing Python entries first, then re-run.!RST!
    pause
    exit /b 1
)
echo !GRN!  [D3/4]  Installation complete.  Exit code: !INST_CODE!!RST!
echo.


:: ── D4 : PATH refresh + stale venv cleanup ───────────────────────────
echo !CYN!  [D4/4]  Refreshing in-session PATH from Windows registry...!RST!
echo !DIM!  [    ]  The installer updated HKCU\Environment and HKLM PATH registry keys.!RST!
echo !DIM!  [    ]  Reading new values via PowerShell so this session sees python.exe!RST!
echo !DIM!  [    ]  immediately - without closing and reopening the console window.!RST!
echo.

:: PowerShell reads the live registry values and writes the combined PATH to
:: a temp file. We read it back into the in-session PATH variable.
powershell -nologo -noprofile -command "[Environment]::GetEnvironmentVariable('PATH','Machine') + ';' + [Environment]::GetEnvironmentVariable('PATH','User')" > "%TEMP%\agetha_newpath.tmp" 2>nul
for /f "usebackq delims=" %%P in ("%TEMP%\agetha_newpath.tmp") do set "PATH=%%P"
del "%TEMP%\agetha_newpath.tmp" >nul 2>&1

:: Confirm the new Python is now reachable and is the x64 variant we installed
python --version >nul 2>&1
if !errorlevel! NEQ 0 (
    echo !YLW!  [WARN]  Python still not found after PATH refresh.!RST!
    echo !DIM!  [    ]  The installer may have used a non-standard directory.!RST!
    echo !DIM!  [    ]  Close this window, reopen it, and run this script again.!RST!
    pause
    exit /b 1
)

set "NEW_ARCH=UNKNOWN"
for /f "usebackq delims=" %%A in (`python -c "import platform; print(platform.machine())"`) do set "NEW_ARCH=%%A"

if /I "!NEW_ARCH!" NEQ "AMD64" (
    echo !YLW!  [WARN]  Python found but reports arch "!NEW_ARCH!" - expected AMD64.!RST!
    echo !DIM!  [    ]  The ARM64 Python may still be earlier on PATH.!RST!
    echo !DIM!  [    ]  Close this window and re-run to start with a clean PATH.!RST!
    pause
    exit /b 1
)
echo !GRN!  [D4/4]  PATH refreshed  -  Python !NEW_ARCH! (x64) is now active.!RST!
echo.

:: ── D4 BONUS: Stale venv detection ───────────────────────────────────
:: If a venv\ directory already exists and was created with the old ARM64
:: Python, the venv's internal python.exe is an ARM64 binary. It will fail
:: to run x64-compiled packages. Detect and remove it now so step [2/6]
:: rebuilds it correctly with the x64 Python we just installed.
if exist "venv\Scripts\python.exe" (
    set "VENV_ARCH=UNKNOWN"
    for /f "usebackq delims=" %%A in (`venv\Scripts\python.exe -c "import platform; print(platform.machine())" 2^>nul`) do set "VENV_ARCH=%%A"
    if /I "!VENV_ARCH!" NEQ "AMD64" (
        echo !YLW!  [VENV]  Existing venv was built with !VENV_ARCH! Python  ^(incompatible^).!RST!
        echo !CYN!  [    ]  Removing stale venv - it will be rebuilt in step [2/6]...!RST!
        rmdir /s /q venv
        echo !GRN!  [ OK ]  Stale venv removed.!RST!
    ) else (
        echo !GRN!  [ OK ]  Existing venv architecture is AMD64 - no rebuild needed.!RST!
    )
    echo.
)

echo !GRN!  [ OK ]  ARM64 platform configuration resolved.!RST!
echo !GRN!  [ OK ]  Proceeding with standard health checks.!RST!
echo.


:: ════════════════════════════════════════════════════════════════════════
::  STANDARD HEALTH CHECKS  [1/6 through 6/6]
::
::  From this point, the script runs identically on all platforms. On ARM64
::  machines, control arrives here in one of three ways:
::    (a) Python was already x64 (AMD64) on PATH - no intervention needed
::    (b) An existing x64 Python was found off-PATH and prepended to it
::    (c) The D1-D4 pipeline downloaded, installed, and verified x64 Python
:: ════════════════════════════════════════════════════════════════════════
:STANDARD_CHECKS
echo !WHT!!BLD!  ┌──────────────────────────────────────────────────────────┐!RST!
echo !WHT!!BLD!  │  Standard System Health Checks  [1/6 – 6/6]              │!RST!
echo !WHT!!BLD!  └──────────────────────────────────────────────────────────┘!RST!
echo.


:: ── [1/6]  Python ────────────────────────────────────────────────────
echo !WHT!  [1 / 6]  Python!RST!
python --version >nul 2>&1
if !errorlevel! NEQ 0 (
    :: 'python' not on PATH - try the py.exe launcher (covers some install configs)
    py -3 --version >nul 2>&1
    if !errorlevel! NEQ 0 (
        echo !RED!  [FAIL]  Python not found on PATH.!RST!
        echo !DIM!  [    ]  https://www.python.org/downloads/!RST!
        pause
        exit /b 1
    )
    set "PYTHON_CMD=py -3"
) else (
    set "PYTHON_CMD=python"
)
for /f "tokens=*" %%V in ('!PYTHON_CMD! --version 2^>^&1') do echo !GRN!  [ OK ]  %%V!RST!
echo.


:: ── [2/6]  Virtual environment ───────────────────────────────────────
echo !WHT!  [2 / 6]  Virtual environment!RST!
if not exist "venv\Scripts\activate.bat" (
    echo !CYN!  [    ]  Not found - creating venv  ^(first run only^)...!RST!
    !PYTHON_CMD! -m venv venv
    if !errorlevel! NEQ 0 (
        echo !RED!  [FAIL]  venv creation failed.  Check write permissions.!RST!
        pause
        exit /b 1
    )
    echo !GRN!  [ OK ]  venv created.!RST!
) else (
    echo !GRN!  [ OK ]  venv already exists.!RST!
)
call venv\Scripts\activate.bat
if !errorlevel! NEQ 0 (
    echo !RED!  [FAIL]  Could not activate venv.  Delete venv\ and re-run.!RST!
    pause
    exit /b 1
)
echo !GRN!  [ OK ]  venv active.!RST!
echo.


:: ── [3/6]  Python packages  (only installs what is missing) ──────────
echo !WHT!  [3 / 6]  Python packages!RST!
set "MISSING="
pip show pillow       >nul 2>&1 || set "MISSING=!MISSING! pillow"
pip show pyautogui    >nul 2>&1 || set "MISSING=!MISSING! pyautogui"
pip show pytesseract  >nul 2>&1 || set "MISSING=!MISSING! pytesseract"
pip show numpy        >nul 2>&1 || set "MISSING=!MISSING! numpy"
pip show pygame       >nul 2>&1 || set "MISSING=!MISSING! pygame"
pip show requests     >nul 2>&1 || set "MISSING=!MISSING! requests"
pip show groq         >nul 2>&1 || set "MISSING=!MISSING! groq"
pip show tkextrafont  >nul 2>&1 || set "MISSING=!MISSING! tkextrafont"
pip show mss          >nul 2>&1 || set "MISSING=!MISSING! mss"
if "!MISSING!"=="" (
    echo !GRN!  [ OK ]  All 9 packages installed.!RST!
) else (
    echo !CYN!  [    ]  Missing:!MISSING!!RST!
    echo !CYN!  [    ]  Installing - please wait...!RST!
    pip install !MISSING! --quiet --disable-pip-version-check
    if !errorlevel! NEQ 0 (
        echo !RED!  [FAIL]  Package install failed.  Run:  pip install!MISSING!!RST!
        pause
        exit /b 1
    )
    echo !GRN!  [ OK ]  Packages installed.!RST!
)
echo.


:: ── [4/6]  Tesseract OCR  (optional - enables screen reading) ────────
echo !WHT!  [4 / 6]  Tesseract OCR  ^(screen reader^)!RST!
set "TESS=0"
where tesseract >nul 2>&1                                          && set "TESS=1"
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe"       set "TESS=1"
if exist "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe" set "TESS=1"
if "!TESS!"=="1" (
    echo !GRN!  [ OK ]  Tesseract found - screen reading enabled.!RST!
) else (
    echo !YLW!  [WARN]  Tesseract not installed - screen reading disabled.!RST!
    echo !DIM!  [    ]  Install: https://github.com/UB-Mannheim/tesseract/wiki!RST!
    echo !DIM!  [    ]  ^(Windows .exe installer - default install path is fine^)!RST!
)
echo.


:: ── [5/6]  Required assets ───────────────────────────────────────────
echo !WHT!  [5 / 6]  Assets  ^( assets\ ^)!RST!
set "ASSET_FAIL=0"
set "ASSET_LIST=angry-static.gif angry.gif error.gif happy-static.gif happy.gif icon.ico idle-1.gif idle-2.gif idle-3.gif loaf.gif sad-static.gif sad.gif sleeping.gif surprised.gif talking-1.gif talking-2.gif talking-3.gif thinking-static.gif thinking.gif barrio.ttf"
for %%F in (!ASSET_LIST!) do (
    if not exist "assets\%%F" (
        echo !YLW!  [MISS]  assets\%%F!RST!
        set "ASSET_FAIL=1"
    )
)
if "!ASSET_FAIL!"=="0" (
    echo !GRN!  [ OK ]  All 20 assets present.!RST!
) else (
    echo !YLW!  [WARN]  Missing assets will cause broken or invisible animations.!RST!
    echo !DIM!  [    ]  Place all .gif and .ttf files inside the assets\ folder.!RST!
)
echo.


:: ── [6/6]  Config & runtime files ────────────────────────────────────
echo !WHT!  [6 / 6]  Config ^& runtime files!RST!

:: Ensure memory\ directory exists (stores long-term memories between sessions)
if not exist "memory\" (
    mkdir memory
    echo !GRN!  [ OK ]  Created memory\!RST!
) else (
    echo !GRN!  [ OK ]  memory\ exists.!RST!
)

:: conversation.txt is wiped on every Agetha launch - just ensure it can exist
if not exist "conversation.txt" type NUL > conversation.txt

:: Config file check - verify a Groq API key or Ollama model is configured
if not exist "config.txt" (
    echo !YLW!  [WARN]  config.txt not found.  Default generated on first run.!RST!
    echo !DIM!  [    ]  Free Groq key: https://console.groq.com!RST!
) else (
    !PYTHON_CMD! -c "import re; txt=open('config.txt',encoding='utf-8',errors='replace').read(); lm=re.search(r'USE_LOCAL_AI\s*=\s*(\S+)',txt); mm=re.search(r'LOCAL_AI_MODEL\s*=\s*(\S+)',txt); km=re.search(r'GROQ_API_KEY\s*=\s*(.+)',txt); kv=km.group(1).strip() if km else ''; print('LOCAL' if lm and lm.group(1).lower()=='yes' and mm and mm.group(1).strip() else 'LOCAL_NO_MODEL' if lm and lm.group(1).lower()=='yes' else 'SET' if len(kv)>20 else 'EMPTY')" > agetha_cfgcheck.tmp 2>nul
    for /f "usebackq tokens=*" %%R in ("agetha_cfgcheck.tmp") do set "CFG_STATUS=%%R"
    del agetha_cfgcheck.tmp >nul 2>&1
    if "!CFG_STATUS!"=="SET"             echo !GRN!  [ OK ]  config.txt - Groq API key configured.!RST!
    if "!CFG_STATUS!"=="LOCAL"           echo !GRN!  [ OK ]  config.txt - Local AI ^(Ollama^) mode active.!RST!
    if "!CFG_STATUS!"=="LOCAL_NO_MODEL" (
        echo !YLW!  [WARN]  USE_LOCAL_AI=yes but LOCAL_AI_MODEL is blank.!RST!
        echo !DIM!  [    ]  Run: ollama list    then set LOCAL_AI_MODEL in config.txt!RST!
    )
    if "!CFG_STATUS!"=="EMPTY" (
        echo !YLW!  [WARN]  GROQ_API_KEY is empty - Agetha will not respond.!RST!
        echo !DIM!  [    ]  Free key: https://console.groq.com!RST!
    )
)
echo.


:: ════════════════════════════════════════════════════════════════════════
::  LAUNCH
:: ════════════════════════════════════════════════════════════════════════
echo !WHT!!BLD!  ╔════════════════════════════════════════════════════════════╗!RST!
echo !WHT!!BLD!  ║  All checks complete.  Launching Agetha...                 ║!RST!
echo !WHT!!BLD!  ╚════════════════════════════════════════════════════════════╝!RST!
echo.

!PYTHON_CMD! main.py

if !errorlevel! NEQ 0 (
    echo.
    echo !RED!  [----]  Agetha exited with code: !errorlevel!!RST!
    echo !DIM!  [    ]  Scroll up in this window to read the crash details.!RST!
    echo.
    pause
)

endlocal
