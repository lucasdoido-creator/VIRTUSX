# VIRTUSX — plataforma de distribuição de marca própria

Documentação completa: **[docs/PLATAFORMA.md](docs/PLATAFORMA.md)**.

**Windows:** `iniciar.bat` (demonstração, http://127.0.0.1:8000, login `admin@central.demo` / `demo1234`) · `iniciar-producao.bat` (operação real, http://127.0.0.1:8001, primeiro acesso cria o administrador). Testes: `verificar.bat`.

**Linux/macOS:**
```bash
pip install -r requirements.txt
APP_ENV=demo ./run.sh
python3 -m pytest
```

CI: cada envio é testado em Windows e Linux (`.github/workflows/ci.yml`); falhas abrem uma issue automaticamente.
