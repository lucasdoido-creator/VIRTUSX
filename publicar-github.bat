@echo off
REM Publica o VIRTUSX no GitHub (rode uma vez). Requer Git e GitHub CLI: winget install Git.Git GitHub.cli
cd /d "%~dp0"
where git >nul 2>nul || (echo Instale o Git: winget install Git.Git & pause & exit /b 1)
where gh  >nul 2>nul || (echo Instale o GitHub CLI: winget install GitHub.cli & pause & exit /b 1)
REM copia a automacao (CI, Dependabot, Claude sob demanda) para o lugar padrao
xcopy /E /I /Y config-github .github >nul
gh auth status >nul 2>nul || gh auth login
if not exist .git (git init -b main)
git add -A
git commit -m "VIRTUSX: atualizacao" 2>nul
gh repo view VIRTUSX >nul 2>nul
if errorlevel 1 (
  gh repo create VIRTUSX --private --source . --push
) else (
  git push -u origin main
)
echo.
echo Pronto. Acompanhe os testes em: 
gh repo view VIRTUSX --web
pause
