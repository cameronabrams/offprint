@echo off
REM Double-click to refresh the mirror in this folder.
REM Prefers uv (which installs the dependencies itself, per the PEP 723
REM header in zotero_source.py); falls back to the py launcher.
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if %errorlevel%==0 (
    uv run --script zotero_source.py --refresh %*
    goto :done
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 zotero_source.py --refresh %*
    goto :done
)

echo Could not find uv or the py launcher on PATH.
echo Install uv from https://docs.astral.sh/uv/ or run the script with your
echo own interpreter:  python zotero_source.py --refresh

:done
echo.
pause
