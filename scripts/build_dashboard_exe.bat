@echo off
setlocal

set ENV_NAME=xmu-rollcall-dashboard
set APP_NAME=XMU-Rollcall-Dashboard

set "CONDA_COMMAND=%CONDA_EXE%"
if not defined CONDA_COMMAND (
  for /f "delims=" %%I in ('where conda 2^>nul') do if not defined CONDA_COMMAND set "CONDA_COMMAND=%%I"
)
if not defined CONDA_COMMAND (
  echo Conda was not found. Open an Anaconda Prompt and try again.
  exit /b 1
)

cd /d "%~dp0\.."

echo Building %APP_NAME%.exe with Conda environment: %ENV_NAME%
echo Project root: %CD%

rem Keep unrelated Qt/ICU DLLs from the caller's PATH out of the frozen app.
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem"

call "%CONDA_COMMAND%" run -n %ENV_NAME% python "%CD%\scripts\build_dashboard.py"

if errorlevel 1 (
  echo Build failed.
  exit /b 1
)

echo.
echo Build complete:
echo %CD%\dist\%APP_NAME%.exe
