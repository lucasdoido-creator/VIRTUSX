@echo off
REM Roda todos os testes automaticos no seu computador.
cd /d "%~dp0"
python -m pip install -r requirements.txt --quiet
python -m pytest -q
pause
