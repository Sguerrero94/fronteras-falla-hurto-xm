@echo off
rem Ejecuta la descarga diaria y guarda el resultado en logs\descarga.log
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist logs mkdir logs
".venv\Scripts\python.exe" descargar.py >> logs\descarga.log 2>&1
