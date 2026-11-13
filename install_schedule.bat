@echo off
REM Register (or re-register) the hourly mirror refresh.
REM Runs as you, only while you are logged on, with no visible window.
setlocal

set TASKNAME=Offprint mirror
REM Renamed from "Mendeley mirror" on 2026-11-13. `remove` deletes both, so a
REM machine registered under the old name can still be cleaned up from here.
set OLDTASKNAME=Mendeley mirror

if /i "%~1"=="remove" (
    schtasks /delete /tn "%TASKNAME%" /f
    schtasks /delete /tn "%OLDTASKNAME%" /f 2>nul
    echo Removed the scheduled task.
    goto :end
)

schtasks /create /f /tn "%TASKNAME%" /sc hourly /mo 1 ^
    /tr "wscript.exe \"%~dp0refresh_quiet.vbs\""

if errorlevel 1 (
    echo.
    echo Could not register the task. If this says access is denied, run this
    echo file from an Administrator command prompt.
    goto :end
)

echo.
echo Registered "%TASKNAME%" -- hourly, starting an hour from now.
echo.
echo   check it:    schtasks /query /tn "%TASKNAME%"
echo   run it now:  schtasks /run   /tn "%TASKNAME%"
echo   remove it:   install_schedule.bat remove
echo.
echo Results land in mirror-status.md; history in .mirror\mirror.log.

:end
echo.
pause
