@echo off
:: Request admin rights (required for screen-capture exclusion to work)
net session >nul 2>&1
if errorlevel 1 (
    echo [AI Copilot] Requesting administrator rights...
    echo [AI Copilot] (Required so overlay is invisible during screen sharing)
    powershell -Command "Start-Process cmd -ArgumentList '/c cd /d \"%~dp0\" && call start.bat' -Verb RunAs"
    exit /b
)

echo.
echo  [AI Copilot] Running as Administrator - overlay will be invisible in screen share
echo.

:: Create venv if needed
if not exist "venv\" (
    echo [1/3] Creating virtual environment...
    python -m venv venv
)

:: Activate and install
echo [2/3] Installing dependencies...
call venv\Scripts\activate.bat
pip install -r requirements.txt --quiet

:: Create .env from example if it doesn't exist
if not exist ".env" (
    if exist ".env.example" (
        echo [3/3] Creating .env from .env.example...
        copy ".env.example" ".env" >nul
        echo [!] .env created. Fill in your API key(s).
        notepad .env
        pause
    )
) else (
    echo [3/3] .env found.
)

echo.
echo  Controls:
echo    Ctrl+Shift+A = show / hide overlay
echo    Alt+P  = pause / resume listening
echo    Alt+C  = clear messages
echo    Alt+Q  = quit
echo    Right-click tray icon for menu
echo.

python main.py
pause
