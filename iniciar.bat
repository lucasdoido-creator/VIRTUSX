@echo off
REM Inicia o VIRTUSX no Windows (duplo clique). Requer Python 3.11+.
cd /d "%~dp0"
python -c "import uvicorn, starlette, httpx" 2>nul
if errorlevel 1 (
  echo Instalando dependencias pela primeira vez...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Falha ao instalar dependencias. Verifique a internet e o pip.
    pause
    exit /b 1
  )
)
python -c "import tzdata" 2>nul || python -m pip install tzdata --quiet
cd /d "%~dp0server"
if "%APP_ENV%"=="" set APP_ENV=demo
echo.
echo Ambiente: %APP_ENV%  -  abra http://127.0.0.1:8000
echo Login demo: admin@central.demo / demo1234
echo.
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 3
pause
