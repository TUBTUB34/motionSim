@echo off
setlocal
cd /d "%~dp0"
if exist .venv-build\Scripts\python.exe goto :dependencies
py -3.14 -m venv .venv-build
if errorlevel 1 goto :failed
:dependencies
.venv-build\Scripts\python -m pip install -r requirements-build.txt
if errorlevel 1 goto :failed
rem Use a fresh work directory so locked files from a previous build cannot block us.
for /f %%G in ('.venv-build\Scripts\python -c "import uuid; print(uuid.uuid4().hex)"') do set "MOTIONSIM_BUILD_ID=%%G"
if not defined MOTIONSIM_BUILD_ID goto :failed
.venv-build\Scripts\python -m PyInstaller --noconfirm --workpath "build\runs\%MOTIONSIM_BUILD_ID%" MotionStudio.spec
if errorlevel 1 goto :failed
start /wait "" dist\URMotionStudio.exe --smoke-test
if errorlevel 1 goto :failed
echo Ready: dist\URMotionStudio.exe
exit /b 0
:failed
echo Build failed. Review the error above. This script uses Python 3.14 with Tcl/Tk.
exit /b 1
