@echo off
rem panzer-build-logic's tool: panzer --help (needs Python 3.11+).
where py >nul 2>nul || goto python
py -3 "%~dp0tools\panzer.py" %*
exit /b
:python
where python >nul 2>nul || goto missing
python "%~dp0tools\panzer.py" %*
exit /b
:missing
echo panzer needs Python 3.11 or newer: https://www.python.org/downloads/ 1>&2
exit /b 1
