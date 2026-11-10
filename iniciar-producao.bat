@echo off
setlocal
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
REM ---- IA gratis local: liga o Ollama e baixa o modelo se faltar
set "MODELO=qwen2.5:3b"
if exist config.local.env for /f "tokens=1,* delims==" %%a in ('findstr /b "OLLAMA_MODEL=" config.local.env') do if not "%%b"=="" set "MODELO=%%b"
where ollama >nul 2>nul
if errorlevel 1 goto semollama
curl -s -o nul http://127.0.0.1:11434/api/tags
if errorlevel 1 (
  echo Ligando o Ollama - IA gratis local...
  start "Ollama" /min ollama serve
  timeout /t 5 /nobreak >nul
)
ollama list | findstr /i /c:"%MODELO%" >nul
if errorlevel 1 (
  echo Baixando o modelo %MODELO% - so na primeira vez, pode levar alguns minutos...
  ollama pull %MODELO%
)
echo IA gratis local: ligada - modelo %MODELO%
goto servidor
:semollama
echo Ollama nao encontrado: o sistema funciona sem IA local - instale em ollama.com para ativar.
:servidor
cd /d "%~dp0server"
set APP_ENV=production
echo.
echo ================== PRODUCAO - dados e acoes reais ==================
echo Abra http://127.0.0.1:8001  -  no primeiro acesso, crie o administrador.
echo Dados ficam em D:\VIRTUSX\data\plataforma_production.sqlite3 - copia de seguranca automatica diaria em data\backups.
echo.
echo Vigia ligado: atualizou arquivos = reinicia sozinho; caiu = volta sozinho. Registro em data\logs\supervisor.log
python rodar.py --host 127.0.0.1 --port 8001
pause
