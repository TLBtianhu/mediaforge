@echo off
rem MediaForge MCP launcher.
rem
rem This file lives at a fixed, user-writable location:
rem   %LOCALAPPDATA%\MediaForge\bin\mediaforge-mcp.cmd
rem
rem It deliberately contains NO baked-in install path: the DSH bundle points at
rem this fixed location only, so one bundle serves a dev checkout, a per-machine
rem install, a per-user install and a portable folder. Resolution order:
rem
rem   1. MEDIAFORGE_HOME                        explicit override
rem   2. %LOCALAPPDATA%\MediaForge\home.txt     install pointer, one line
rem   3. HKCU\Software\MediaForge InstallDir    install pointer, registry
rem   4. %ProgramFiles%\MediaForge              default install location
rem   5. %ProgramFiles(x86)%\MediaForge
rem
rem DSH scrubs ambient names matching KEY|PASSWORD|SECRET|TOKEN and DSH_*, so
rem MEDIAFORGE_HOME survives that scrub. Keep this file ASCII-only: cmd.exe reads
rem it in the OEM code page.
setlocal EnableExtensions

set "MF_ROOT="

if defined MEDIAFORGE_HOME if exist "%MEDIAFORGE_HOME%\python\agent_bridge.py" set "MF_ROOT=%MEDIAFORGE_HOME%"

if not defined MF_ROOT (
  set "MF_HOME_FILE=%LOCALAPPDATA%\MediaForge\home.txt"
  if exist "%MF_HOME_FILE%" (
    for /f "usebackq delims=" %%L in ("%MF_HOME_FILE%") do (
      if not defined MF_ROOT if exist "%%~L\python\agent_bridge.py" set "MF_ROOT=%%~L"
    )
  )
)

if not defined MF_ROOT (
  for /f "tokens=2,*" %%A in ('reg query "HKCU\Software\MediaForge" /v InstallDir 2^>nul ^| findstr /i "InstallDir"') do (
    if not defined MF_ROOT if exist "%%~B\python\agent_bridge.py" set "MF_ROOT=%%~B"
  )
)

if not defined MF_ROOT if defined ProgramFiles if exist "%ProgramFiles%\MediaForge\python\agent_bridge.py" set "MF_ROOT=%ProgramFiles%\MediaForge"
if not defined MF_ROOT if defined ProgramW6432 if exist "%ProgramW6432%\MediaForge\python\agent_bridge.py" set "MF_ROOT=%ProgramW6432%\MediaForge"
if not defined MF_ROOT if defined ProgramFiles(x86) if exist "%ProgramFiles(x86)%\MediaForge\python\agent_bridge.py" set "MF_ROOT=%ProgramFiles(x86)%\MediaForge"

if not defined MF_ROOT (
  echo MediaForge MCP launcher: cannot find a MediaForge install. 1>&2
  echo   Looked for python\agent_bridge.py under MEDIAFORGE_HOME, 1>&2
  echo   %%LOCALAPPDATA%%\MediaForge\home.txt, HKCU\Software\MediaForge\InstallDir, 1>&2
  echo   and %%ProgramFiles%%\MediaForge. 1>&2
  echo   Fix: run setup.ps1 -AppRoot "path\to\MediaForge" from the DSH bundle, 1>&2
  echo   or set MEDIAFORGE_HOME. 1>&2
  exit /b 2
)

rem stdio is required by the DSH transport; an explicit argument list wins so the
rem same launcher can serve manual testing (e.g. --port 1421).
set "MF_ARGS=%*"
if not defined MF_ARGS set "MF_ARGS=--stdio"

if defined MEDIAFORGE_PYTHON if exist "%MEDIAFORGE_PYTHON%" (
  "%MEDIAFORGE_PYTHON%" "%MF_ROOT%\python\agent_bridge.py" %MF_ARGS%
  exit /b %ERRORLEVEL%
)
if exist "%MF_ROOT%\python-runtime\python.exe" (
  "%MF_ROOT%\python-runtime\python.exe" "%MF_ROOT%\python\agent_bridge.py" %MF_ARGS%
  exit /b %ERRORLEVEL%
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%MF_ROOT%\python\agent_bridge.py" %MF_ARGS%
  exit /b %ERRORLEVEL%
)
python "%MF_ROOT%\python\agent_bridge.py" %MF_ARGS%
exit /b %ERRORLEVEL%
