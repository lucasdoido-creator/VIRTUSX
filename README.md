# VIRTUSX — plataforma de distribuição de marca própria

Documentação completa: **[docs/PLATAFORMA.md](docs/PLATAFORMA.md)**.

**Windows:** dois cliques em `iniciar.bat` → abra http://127.0.0.1:8000 (login demo `admin@central.demo` / `demo1234`). Testes: `verificar.bat`.

**Linux/macOS:**
```bash
pip install -r requirements.txt
APP_ENV=demo ./run.sh
python3 -m pytest
```

CI: cada envio é testado em Windows e Linux (`.github/workflows/ci.yml`); falhas abrem uma issue automaticamente.
