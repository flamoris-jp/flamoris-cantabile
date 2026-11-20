@echo off
setlocal
set "repo=flamoris-jp/flamoris-cantabile"
set "run="
for /f "delims=" %%I in ('gh run list -R %repo% -w cantabile-client.yml --branch main --event push --status success --limit 1 --json databaseId --jq ".[0].databaseId // empty"') do set "run=%%I"
if not defined run goto previous

set "dest=%~dp0CantabileClient-%run%"
if exist "%dest%\.complete" goto ready
gh run download %run% -R %repo% -n CantabileClient-win-x64 -D "%dest%"
if errorlevel 1 goto previous
if not exist "%dest%\bin\cantabile-client.exe" goto previous
echo %run%>"%dest%\.complete"

:ready
echo %dest%>"%~dp0CantabileClient-current.txt"
goto launch

:previous
echo Update unavailable. Trying the installed version.
set "dest=%~dp0CantabileClient"
if exist "%~dp0CantabileClient-current.txt" set /p "dest="<"%~dp0CantabileClient-current.txt"

:launch
if not exist "%dest%\bin\cantabile-client.exe" (
    echo CantabileClient is not installed yet.
    pause
    exit /b 1
)
cd /d "%dest%" || exit /b 1
powershell.exe -NoLogo -NoProfile -NoExit -Command "& '.\bin\cantabile-client.exe' --help"
