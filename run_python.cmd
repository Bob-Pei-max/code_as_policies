@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0.conda-env\python.exe" (
    echo Missing .conda-env. Run: conda env create --prefix ./.conda-env --file environment.yml 1>&2
    exit /b 1
)

set "CAP_CONDA="
if defined CONDA_EXE if exist "%CONDA_EXE%" set "CAP_CONDA=%CONDA_EXE%"
if not defined CAP_CONDA for /f "delims=" %%I in ('where conda.exe 2^>nul') do if not defined CAP_CONDA set "CAP_CONDA=%%I"
if not defined CAP_CONDA (
    echo Conda was not found. Use an Anaconda Prompt or set CONDA_EXE to conda.exe. 1>&2
    exit /b 1
)

"%CAP_CONDA%" run --prefix "%~dp0.conda-env" --no-capture-output python %*
exit /b %ERRORLEVEL%
