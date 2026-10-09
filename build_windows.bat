@echo off
setlocal
cd /d "%~dp0"
py -3.12 -m venv .venv-build
if errorlevel 1 goto :failed
.venv-build\Scripts\python -m pip install -r requirements-build.txt
if errorlevel 1 goto :failed
.venv-build\Scripts\python -m PyInstaller --noconfirm --clean MotionStudio.spec
if errorlevel 1 goto :failed
start /wait "" dist\URMotionStudio.exe --smoke-test
if errorlevel 1 goto :failed
echo Ready: dist\URMotionStudio.exe
exit /b 0
:failed
echo Build failed. Install Python 3.12 64-bit with Tcl/Tk and review the error above.
exit /b 1
