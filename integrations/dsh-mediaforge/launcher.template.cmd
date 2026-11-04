@echo off
rem MediaForge MCP launcher.
rem
rem This file lives at a fixed, user-writable location:
rem   %USERPROFILE%\.mediaforge\bin\mediaforge-mcp.cmd
rem
rem It deliberately contains NO baked-in install path: the DSH bundle points at
rem this fixed location only, so one bundle serves a dev checkout, a per-machine
rem install, a per-user install and a portable folder. Resolution order:
rem
rem   1. MEDIAFORGE_HOME                        explicit override
rem   2. HKCU\Software\MediaForge InstallDir    install pointer, registry
rem   3. %LOCALAPPDATA%\Programs\MediaForge     per-user install (the default)
rem   4. %ProgramFiles%\MediaForge              per-machine install
rem   5. %ProgramFiles(x86)%\MediaForge
rem
rem DSH scrubs ambient names matching KEY|PASSWORD|SECRET|TOKEN and DSH_*, so
rem MEDIAFORGE_HOME survives that scrub. Keep this file ASCII-only: cmd.exe reads
rem it in the OEM code page.
setlocal EnableExtensions

set "MF_ROOT="

if defined MEDIAFORGE_HOME if exist "%MEDIAFORGE_HOME%\python\agent_bridge.py" set "MF_ROOT=%MEDIAFORGE_HOME%"

if not defined MF_ROOT (
  for /f "tokens=2,*" %%A in ('reg query "HKCU\Software\MediaForge" /v InstallDir 2^>nul ^| findstr /i "InstallDir"') do (
    if not defined MF_ROOT if exist "%%~B\python\agent_bridge.py" set "MF_ROOT=%%~B"
  )
)

if not defined MF_ROOT if defined LOCALAPPDATA if exist "%LOCALAPPDATA%\Programs\MediaForge\python\agent_bridge.py" set "MF_ROOT=%LOCALAPPDATA%\Programs\MediaForge"
if not defined MF_ROOT if defined ProgramFiles if exist "%ProgramFiles%\MediaForge\python\agent_bridge.py" set "MF_ROOT=%ProgramFiles%\MediaForge"
if not defined MF_ROOT if defined ProgramW6432 if exist "%ProgramW6432%\MediaForge\python\agent_bridge.py" set "MF_ROOT=%ProgramW6432%\MediaForge"
if not defined MF_ROOT if defined ProgramFiles(x86) if exist "%ProgramFiles(x86)%\MediaForge\python\agent_bridge.py" set "MF_ROOT=%ProgramFiles(x86)%\MediaForge"

if not defined MF_ROOT (
  echo MediaForge MCP launcher: cannot find a MediaForge install. 1>&2
  echo   Looked for python\agent_bridge.py under MEDIAFORGE_HOME, 1>&2
  echo   HKCU\Software\MediaForge\InstallDir, 1>&2
  echo   %%LOCALAPPDATA%%\Programs\MediaForge and %%ProgramFiles%%\MediaForge. 1>&2
  echo   Download: https://www.modelscope.cn/datasets/TLBtianhu/MediaForge-Dist 1>&2
  echo   Fix: run setup.ps1 -AppRoot "path\to\MediaForge" from the DSH bundle, 1>&2
  echo   or set MEDIAFORGE_HOME. 1>&2
  exit /b 2
)

rem Set the shared data/model/runtime roots for the bridge. Explicit environment
rem overrides always win. Installed roots use app/../{data,models,runtime-envs};
rem portable roots use the same layout beside the marker; development uses .dev-data.
if /i "%MF_ROOT:~-4%"=="\app" (
  for %%I in ("%MF_ROOT%\..") do set "MF_INSTALL_ROOT=%%~fI"
  if not defined MEDIAFORGE_DATA_ROOT set "MEDIAFORGE_DATA_ROOT=%MF_INSTALL_ROOT%\data"
  if not defined MEDIAFORGE_MODEL_DIR set "MEDIAFORGE_MODEL_DIR=%MF_INSTALL_ROOT%\models"
  if not defined MEDIAFORGE_RUNTIME_ENVS_DIR set "MEDIAFORGE_RUNTIME_ENVS_DIR=%MF_INSTALL_ROOT%\runtime-envs"
) else if exist "%MF_ROOT%\mediaforge.portable" (
  if not defined MEDIAFORGE_DATA_ROOT set "MEDIAFORGE_DATA_ROOT=%MF_ROOT%\data"
  if not defined MEDIAFORGE_MODEL_DIR set "MEDIAFORGE_MODEL_DIR=%MF_ROOT%\models"
  if not defined MEDIAFORGE_RUNTIME_ENVS_DIR set "MEDIAFORGE_RUNTIME_ENVS_DIR=%MF_ROOT%\runtime-envs"
) else (
  if not defined MEDIAFORGE_DATA_ROOT set "MEDIAFORGE_DATA_ROOT=%MF_ROOT%\.dev-data"
  if not defined MEDIAFORGE_MODEL_DIR set "MEDIAFORGE_MODEL_DIR=%MF_ROOT%\.dev-data\models"
  if not defined MEDIAFORGE_RUNTIME_ENVS_DIR set "MEDIAFORGE_RUNTIME_ENVS_DIR=%MF_ROOT%\.dev-data\runtime-envs"
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
