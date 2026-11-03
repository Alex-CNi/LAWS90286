@echo off
rem Set up the app in its own virtual environment (.venv) on Windows: run once, from the repository root.
rem   setup.bat
rem Afterwards, start the app with:  run.bat   (or: .venv\Scripts\activate  then  streamlit run home.py)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating .venv...
  py -3 -m venv .venv || python -m venv .venv
)
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
".venv\Scripts\python.exe" -m pip install pytest >nul
echo.
echo Ready. Start the app with:  run.bat
echo Run the tests with:         .venv\Scripts\python.exe -m pytest -q
