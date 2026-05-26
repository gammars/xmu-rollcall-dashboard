# XMU Rollcall Dashboard Package

This directory contains the Python package used by the desktop dashboard.
Install runtime dependencies from the repository root with:

```powershell
conda run -n xmu-rollcall-dashboard python -m pip install -r .\xmu-rollcall-cli\requirements.txt pyinstaller
```

Run the dashboard during development:

```powershell
$env:PYTHONPATH=(Resolve-Path .\xmu-rollcall-cli).Path
conda run -n xmu-rollcall-dashboard python -m xmu_rollcall.desktop
```
