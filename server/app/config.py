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


def load_settings(**overrides) -> Settings:
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
