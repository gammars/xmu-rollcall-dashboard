@echo off
setlocal

set ENV_NAME=xmu-rollcall-dashboard
set APP_NAME=XMU-Rollcall-Dashboard-Courseware

cd /d "%~dp0\.."

echo Building %APP_NAME%.exe with Conda environment: %ENV_NAME%
echo Project root: %CD%

conda run -n %ENV_NAME% python -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --onefile ^
  --name %APP_NAME% ^
  --paths "%CD%\xmu-rollcall-cli" ^
  --collect-submodules xmu_rollcall ^
  --collect-submodules xmulogin ^
  "%CD%\scripts\xmu_dashboard_launcher.py"

if errorlevel 1 (
  echo Build failed.
  exit /b 1
)

echo.
echo Build complete:
echo %CD%\dist\%APP_NAME%.exe
