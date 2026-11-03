@echo off
rem Start the app from the virtual environment made by setup.bat (any extra arguments go to streamlit).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo No .venv yet: run   setup.bat   first.
  exit /b 1
)
".venv\Scripts\python.exe" -m streamlit run home.py %*
