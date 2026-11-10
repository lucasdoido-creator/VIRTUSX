"""Configuração por ambiente. Credenciais vêm SOMENTE de variáveis de ambiente do servidor."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENVIRONMENTS = ("demo", "sandbox", "production")
ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    env: str = "demo"
    data_dir: Path = ROOT / "data"
    webhook_secret_demo: str = ""
    session_hours: int = 12
    scheduler_enabled: bool = True
    scheduler_interval_s: float = 5.0
    demo_webhook_delay_s: float = 1.5
    # Integrações reais (pendentes): ficam vazias até o administrador configurar no servidor.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    anthropic_api_key: str = ""
    # Cérebro misto: IA local gratuita (Ollama) no dia a dia; Claude só em decisões, com teto mensal.
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:3b"
    decision_model: str = "claude-haiku-5-5"
    brain_monthly_cap_usd: float = 2.0
    brain_daily_cap_usd: float = 0.20
    routine_fallback_to_claude: bool = False
    research_enabled: bool = True
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_api_version: str = "v24.0"
    hq_lat: float = -23.5505
    hq_lng: float = -46.6333
    webhook_tolerance_s: int = 300
    max_webhook_attempts: int = 5
    reservation_ttl_min: int = 30
    freshness_hours: float = 24.0
    extra: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        # Um arquivo de banco por ambiente: dados de demonstração nunca se misturam com produção.
        return self.data_dir / f"plataforma_{self.env}.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / f"anexos_{self.env}"

    @property
    def is_demo(self) -> bool:
        return self.env == "demo"


_FROM_FILE: set = set()
_OVERRIDDEN: set = set()


def _load_local_env():
    """Lê config.local.env (fica só no seu computador; não vai para o GitHub). O arquivo tem prioridade sobre variáveis do sistema."""
    p = ROOT / "config.local.env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():  # utf-8-sig tolera BOM do Bloco de Notas
        line = line.strip()
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'").strip()
            if not v:
                continue
            if k in os.environ and os.environ[k] != v:
                _OVERRIDDEN.add(k)   # o arquivo vence uma variável antiga do Windows com o mesmo nome
            os.environ[k] = v
            _FROM_FILE.add(k)


def load_settings(**overrides) -> Settings:
    _load_local_env()
    env = os.environ.get("APP_ENV", "demo")
    if env not in ENVIRONMENTS:
        raise RuntimeError(f"APP_ENV inválido: {env}")
    s = Settings(
        env=env,
        data_dir=Path(os.environ.get("DATA_DIR", str(ROOT / "data"))),
        webhook_secret_demo=os.environ.get("DEMO_WEBHOOK_SECRET", ""),
        stripe_secret_key=os.environ.get("STRIPE_SECRET_KEY", ""),
        stripe_webhook_secret=os.environ.get("STRIPE_WEBHOOK_SECRET", ""),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        ollama_url=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "qwen2.5:3b"),
        decision_model=os.environ.get("BRAIN_DECISION_MODEL", "claude-haiku-5-5"),
        brain_monthly_cap_usd=float(os.environ.get("BRAIN_MONTHLY_CAP_USD", "2")),
        brain_daily_cap_usd=float(os.environ.get("BRAIN_DAILY_CAP_USD", "0.20")),
        routine_fallback_to_claude=os.environ.get("BRAIN_ROUTINE_FALLBACK_CLAUDE", "0") == "1",
        research_enabled=os.environ.get("RESEARCH", "1") == "1",
        whatsapp_token=os.environ.get("WHATSAPP_TOKEN", ""),
        whatsapp_phone_number_id=os.environ.get("WHATSAPP_PHONE_NUMBER_ID", ""),
        whatsapp_app_secret=os.environ.get("WHATSAPP_APP_SECRET", ""),
        whatsapp_verify_token=os.environ.get("WHATSAPP_VERIFY_TOKEN", ""),
        whatsapp_api_version=os.environ.get("WHATSAPP_API_VERSION", "v24.0"),
        hq_lat=float(os.environ.get("HQ_LAT", "-23.5505")),
        hq_lng=float(os.environ.get("HQ_LNG", "-46.6333")),
        scheduler_enabled=os.environ.get("SCHEDULER", "1") == "1",
        scheduler_interval_s=float(os.environ.get("SCHEDULER_INTERVAL_S", "5")),
    )
    for k, v in overrides.items():
        setattr(s, k, v)
    if not s.webhook_secret_demo:
        # Segredo local gerado por instalação; nunca enviado ao navegador.
        s.data_dir.mkdir(parents=True, exist_ok=True)
        p = s.data_dir / f".demo_webhook_secret_{s.env}"
        if not p.exists():
            p.write_text(os.urandom(32).hex())
        s.webhook_secret_demo = p.read_text().strip()
    if s.env == "production" and s.is_demo:
        raise RuntimeError("configuração inconsistente")
    return s
