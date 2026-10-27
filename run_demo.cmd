@echo off
setlocal
cd /d "%~dp0"
call "%~dp0run_python.cmd" "%~dp0run_offline.py" %*
exit /b %ERRORLEVEL%
