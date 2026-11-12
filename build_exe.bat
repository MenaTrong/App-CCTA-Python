@echo off
rem Package CCTA Alert into a single .exe (needs: pip install pyinstaller oracledb)
rem oracledb (thin mode) uses cryptography from compiled code, so PyInstaller must be told to include it.
setlocal
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean --onefile --windowed --name CctaAlert --icon ccta_alert\assets\ccta.ico --add-data "ccta_alert\assets\ccta.ico;ccta_alert\assets" --exclude-module PIL --exclude-module numpy ^
  --collect-submodules oracledb --collect-submodules cryptography --hidden-import typing_extensions main.py
if errorlevel 1 (echo Build FAILED & exit /b 1)
echo Build OK: dist\CctaAlert.exe
