@echo off
setlocal
cd /d "%~dp0"
python openjk.py
if errorlevel 1 pause
