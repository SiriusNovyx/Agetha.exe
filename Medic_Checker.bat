@echo off
setlocal EnableDelayedExpansion
title Agetha.exe HealthCheck
color 0A
cls

echo.
echo  ================================================================
echo   AGETHA.EXE  ^|  Startup ^& Health Check
echo  ================================================================
echo.

:: ─────────────────────────────────────────────────────────────────
::  PRE-FLIGHT: Are we in the right folder?
:: ─────────────────────────────────────────────────────────────────
if not exist "main.py" (
color 0C
echo  [FAIL]  main.py not found in this folder.
echo  [    ]  Place run.bat in the same directory as main.py.
echo.
pause
exit /b 1
)

:: ─────────────────────────────────────────────────────────────────
::  1 / 6  Python Check
:: ─────────────────────────────────────────────────────────────────
echo  [1 / 6]  Python Check
python --version >nul 2>&1
if !errorlevel! NEQ 0 (
:: Fallback check for 'py' launcher
py -3 --version >nul 2>&1
if !errorlevel! NEQ 0 (
color 0C
echo.
echo  [FAIL]  Python not found on this machine.
echo  [    ]  Download: https://www.python.org/downloads/
echo  [    ]  During setup, tick "Add Python to PATH".
echo.
pause
exit /b 1
)
set "PYTHON_CMD=py -3"
) else (
set "PYTHON_CMD=python"
)

for /f "tokens=*" %%V in ('!PYTHON_CMD! --version 2^>^&1') do echo  [ OK ]  %%V
echo.

:: ─────────────────────────────────────────────────────────────────
::  2 / 6  Virtual Environment
:: ─────────────────────────────────────────────────────────────────
echo  [2 / 6]  Virtual environment
if not exist "venv\Scripts\activate.bat" (
echo  [    ]  Not found - creating venv ^(first run only^)...
!PYTHON_CMD! -m venv venv
if !errorlevel! NEQ 0 (
color 0C
echo  [FAIL]  venv creation failed.
echo  [    ]  Check that you have write access to this folder.
pause
exit /b 1
)
echo  [ OK ]  venv created.
) else (
echo  [ OK ]  venv already exists, skipping creation.
)

call venv\Scripts\activate.bat
if !errorlevel! NEQ 0 (
color 0C
echo  [FAIL]  Could not activate venv.
echo  [    ]  Try deleting the venv\ folder and running again.
pause
exit /b 1
)
echo  [ OK ]  venv active.
echo.

:: ─────────────────────────────────────────────────────────────────
::  3 / 6  Python Packages  (only installs what is missing)
:: ─────────────────────────────────────────────────────────────────
echo  [3 / 6]  Python packages
set "MISSING="

pip show pillow       >nul 2>&1 || set "MISSING=!MISSING! pillow"
pip show pyautogui    >nul 2>&1 || set "MISSING=!MISSING! pyautogui"
pip show pytesseract  >nul 2>&1 || set "MISSING=!MISSING! pytesseract"
pip show numpy        >nul 2>&1 || set "MISSING=!MISSING! numpy"
pip show pygame       >nul 2>&1 || set "MISSING=!MISSING! pygame"
pip show requests     >nul 2>&1 || set "MISSING=!MISSING! requests"
pip show groq         >nul 2>&1 || set "MISSING=!MISSING! groq"
pip show mss          >nul 2>&1 || set "MISSING=!MISSING! mss"
pip show tkextrafont  >nul 2>&1 || set "MISSING=!MISSING! tkextrafont"

if "!MISSING!"=="" (
echo  [ OK ]  All required packages already installed.
) else (
echo  [    ]  Missing:!MISSING!
echo  [    ]  Installing - this may take a moment...
pip install !MISSING! --quiet --disable-pip-version-check
if !errorlevel! NEQ 0 (
color 0C
echo.
echo  [FAIL]  Package installation failed.
echo  [    ]  Check your internet connection and try again.
echo  [    ]  Or run manually: pip install!MISSING!
pause
exit /b 1
)
echo  [ OK ]  Packages installed successfully.
)
echo.

:: ─────────────────────────────────────────────────────────────────
::  4 / 6  Tesseract OCR  (screen reader - optional but recommended)
:: ─────────────────────────────────────────────────────────────────
echo  [4 / 6]  Tesseract OCR  (screen reader)
set "TESS=0"
where tesseract >nul 2>&1                                          && set "TESS=1"
if exist "C:\Program Files\Tesseract-OCR\tesseract.exe"       set "TESS=1"
if exist "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe" set "TESS=1"

if "!TESS!"=="1" (
echo  [ OK ]  Tesseract found.  Screen reading is enabled.
) else (
color 0E
echo  [WARN]  Tesseract is not installed.
echo  [    ]  Agetha will run but cannot read your screen.
echo  [    ]  Install it here ^(use the Windows .exe installer^):
echo  [    ]  https://github.com/UB-Mannheim/tesseract/wiki
echo  [    ]  Default install path is fine - no extra config needed.
color 0A
)
echo.

:: ─────────────────────────────────────────────────────────────────
::  5 / 6  Required Assets
:: ─────────────────────────────────────────────────────────────────
echo  [5 / 6]  Assets  ( assets\ )
set "ASSET_FAIL=0"
set "ASSET_LIST=angry-static.gif angry.gif error.gif happy-static.gif happy.gif icon.ico idle-1.gif idle-2.gif idle-3.gif loaf.gif sad-static.gif sad.gif sleeping.gif surprised.gif talking-1.gif talking-2.gif talking-3.gif thinking-static.gif thinking.gif barrio.ttf"

for %%F in (!ASSET_LIST!) do (
    if not exist "assets\%%F" (
        echo  [MISS]  assets\%%F
        set "ASSET_FAIL=1"
    )
)

if "!ASSET_FAIL!"=="0" (
echo  [ OK ]  All 20 required assets found.
) else (
color 0E
echo  [WARN]  One or more assets are missing.
echo  [    ]  Missing files will cause broken or invisible animations.
echo  [    ]  Place all .gif and .ttf files inside the assets\ folder.
color 0A
)
echo.

:: ─────────────────────────────────────────────────────────────────
::  6 / 6  Config, Directories, Runtime Files
:: ─────────────────────────────────────────────────────────────────
echo  [6 / 6]  Config ^& runtime files

:: memory\ - stores long-term memories between sessions
if not exist "memory\" (
mkdir memory
echo  [ OK ]  memory\ directory created.
) else (
echo  [ OK ]  memory\ exists.
)

:: config.txt - check if API key or local AI is configured
if not exist "config.txt" (
color 0E
echo  [WARN]  config.txt not found.
echo  [    ]  Agetha will generate a default config on first launch.
echo  [    ]  You will need to add your Groq API key before she can respond.
echo  [    ]  Free key: https://console.groq.com
color 0A
) else (
    :: Use Python (already active in venv) for a reliable key check
    %PYTHON_CMD% -c "import re; txt=open('config.txt',encoding='utf-8',errors='replace').read(); local_m=re.search(r'USE_LOCAL_AI\s*=\s*(\S+)',txt); print('LOCAL' if local_m and local_m.group(1).lower()=='yes' and (lambda x: x.group(1).strip() if x else '')(re.search(r'LOCAL_AI_MODEL\s*=\s*(\S+)',txt)) else 'LOCAL_NO_MODEL' if local_m and local_m.group(1).lower()=='yes' else 'SET' if len((lambda x: x.group(1).strip() if x else '')(re.search(r'GROQ_API_KEY\s*=\s*(.+)',txt)))>20 else 'EMPTY')" > agetha_cfgcheck.tmp 2>nul

    for /f "tokens=*" %%R in (agetha_cfgcheck.tmp) do set "CFG_STATUS=%%R"
    del agetha_cfgcheck.tmp >nul 2>&1

if "!CFG_STATUS!"=="SET" (
    echo  [ OK ]  config.txt  -  Groq API key is configured.
) else if "!CFG_STATUS!"=="LOCAL" (
    echo  [ OK ]  config.txt  -  Local AI ^(Ollama^) mode is set.
) else if "!CFG_STATUS!"=="LOCAL_NO_MODEL" (
    color 0E
    echo  [WARN]  USE_LOCAL_AI = yes but LOCAL_AI_MODEL is blank.
    echo  [    ]  Run: ollama list   to see what is installed.
    echo  [    ]  Then set LOCAL_AI_MODEL = ^<model^> in config.txt
    color 0A
) else if "!CFG_STATUS!"=="EMPTY" (
    color 0E
    echo  [WARN]  GROQ_API_KEY is empty in config.txt.
    echo  [    ]  Agetha will not respond without it.
    echo  [    ]  Free key:  https://console.groq.com
    echo  [    ]  Paste it after: GROQ_API_KEY = 
    color 0A
) else (
    color 0E
    echo  [WARN]  Could not read config.txt.
    color 0A
)


)
echo.

:: ─────────────────────────────────────────────────────────────────
::  All checks complete
:: ─────────────────────────────────────────────────────────────────
echo  ================================================================
echo   Health check done.  Launching Agetha...
echo  ================================================================
echo.

%PYTHON_CMD% main.py

if !errorlevel! NEQ 0 (
    color 0C
    echo.
    echo  [----]  Agetha exited with error code: !errorlevel!
    echo  [----]  Scroll up to read the crash details.
    echo.
    pause
)

endlocal
