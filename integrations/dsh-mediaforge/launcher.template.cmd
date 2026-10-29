@echo off
rem MediaForge MCP 启动器（由 MediaForge 安装包生成，请勿手改路径）
rem 目标：让任何 MCP 客户端用固定路径就能启动 MediaForge 的 agent bridge。
setlocal
set "MF_ROOT=__MEDIAFORGE_ROOT__"
if not exist "%MF_ROOT%\python\agent_bridge.py" (
  echo MediaForge MCP launcher: agent_bridge.py not found under "%MF_ROOT%" 1>&2
  exit /b 2
)
if defined MEDIAFORGE_PYTHON (
  "%MEDIAFORGE_PYTHON%" "%MF_ROOT%\python\agent_bridge.py" --stdio
  exit /b %ERRORLEVEL%
)
if exist "%MF_ROOT%\python-runtime\python.exe" (
  "%MF_ROOT%\python-runtime\python.exe" "%MF_ROOT%\python\agent_bridge.py" --stdio
  exit /b %ERRORLEVEL%
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%MF_ROOT%\python\agent_bridge.py" --stdio
  exit /b %ERRORLEVEL%
)
python "%MF_ROOT%\python\agent_bridge.py" --stdio
exit /b %ERRORLEVEL%
