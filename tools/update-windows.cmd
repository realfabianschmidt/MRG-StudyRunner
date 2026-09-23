@echo off
rem Update Study Runner from the terminal. Stop Study Runner first (Ctrl+C).
rem Everything runs from one line: the update replaces this tools folder, and cmd
rem reads batch files line by line.
if not exist "%~dp0..\.venv\Scripts\python.exe" (echo Study Runner is not installed here. Run tools\install-windows.cmd first. & exit /b 1)
"%~dp0..\.venv\Scripts\python.exe" "%~dp0update_study_runner.py" %* & exit /b
