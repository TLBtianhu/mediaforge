@echo off
setlocal
set "MF_ROOT=%~dp0.."
if defined MEDIAFORGE_PYTHON (
  "%MEDIAFORGE_PYTHON%" "%MF_ROOT%\python\agent_bridge.py" --stdio
) else (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 "%MF_ROOT%\python\agent_bridge.py" --stdio
  ) else (
    python "%MF_ROOT%\python\agent_bridge.py" --stdio
  )
)
