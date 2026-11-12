@echo off
rem Sets ASSIST up once: makes the project's own Python (.venv) from Python 3.12 (64-bit) and installs the pinned,
rem hash-checked Qt listed in requirements.txt. Double-click it, or run it from the project folder.
rem Downloading Qt from PyPI, done by pip, is the only step that uses the network. ASSIST itself never does.
rem It creates nothing outside this folder. Run it again at any time: it reuses a .venv that's fine.
setlocal
cd /d "%~dp0"

echo Looking for Python 3.12 (64-bit)...
call py -3.12 -c "import struct, sys; sys.exit(0 if struct.calcsize('P') == 8 else 64)" >nul 2>nul
set "RESULT=%errorlevel%"
if "%RESULT%"=="64" goto python_32bit
if not "%RESULT%"=="0" goto no_python

if exist ".venv\Scripts\python.exe" goto check_venv
if exist ".venv" goto bad_venv
echo Making the project's Python in .venv...
call py -3.12 -m venv .venv
if errorlevel 1 goto venv_failed
goto install

:check_venv
".venv\Scripts\python.exe" -c "import struct, sys; sys.exit(0 if sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8 else 1)" >nul 2>nul
if errorlevel 1 goto bad_venv
echo Using the project's Python already in .venv.

:install
echo.
echo Downloading and installing Qt (PySide6 6.12.0) from PyPI, the Python Package Index.
echo This is the only step that uses the network. Each file is checked against requirements.txt.
".venv\Scripts\python.exe" -m pip install --require-hashes --only-binary=:all: --disable-pip-version-check --no-input -r requirements.txt
if errorlevel 1 goto pip_failed
".venv\Scripts\python.exe" -c "import PySide6.QtWidgets" >nul 2>nul
if errorlevel 1 goto qt_failed

echo.
echo ASSIST is set up. Start it by double-clicking ASSIST.cmd.
echo For a desktop shortcut: right-click ASSIST.cmd, then Send to, Desktop (create shortcut).
pause
exit /b 0

:no_python
echo.
echo Python 3.12 wasn't found. ASSIST needs Python 3.12 (64-bit); other versions won't do.
echo Install it, then run Setup.cmd again. Either:
echo     with Python's install manager:  py install 3.12
echo     or the Python 3.12 "Windows installer (64-bit)" from python.org
pause
exit /b 1

:python_32bit
echo.
echo Python 3.12 was found, but it's the 32-bit version. ASSIST's Qt needs the 64-bit Python 3.12.
echo Install the 64-bit Python 3.12, then run Setup.cmd again.
pause
exit /b 1

:bad_venv
echo.
echo There's a .venv folder here, but it isn't a working Python 3.12 (64-bit).
echo Delete the .venv folder in the project folder, then run Setup.cmd again.
pause
exit /b 1

:venv_failed
echo.
echo Making .venv failed (see the message above). Delete the .venv folder if there is one, then run Setup.cmd again.
pause
exit /b 1

:pip_failed
echo.
echo Installing Qt failed (see the message above). Check the internet connection, then run Setup.cmd again.
echo A "hashes" error means a downloaded file didn't match requirements.txt; it wasn't installed.
pause
exit /b 1

:qt_failed
echo.
echo Qt was installed but doesn't load. Delete the .venv folder, then run Setup.cmd again.
pause
exit /b 1
