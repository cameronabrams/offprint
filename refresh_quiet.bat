@echo off
REM Non-interactive refresh, for the scheduled task. No pause, no prompts:
REM output goes to .mirror\mirror.log and mirror-status.md.
REM
REM There is no --quiet. The retired refresh had one, for a flag that also
REM suppressed prompts; this one never prompts, and refresh_quiet.vbs is what
REM keeps the window off the screen.
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if %errorlevel%==0 (
    uv run --script zotero_source.py --refresh %*
    exit /b %errorlevel%
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 zotero_source.py --refresh %*
    exit /b %errorlevel%
)

exit /b 9
