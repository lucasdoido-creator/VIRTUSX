"""Agente nunca trava em tarefa com checklist; chave da Claude conferida sem custo."""
import pytest

from app import brain
from app.core import R


def test_ana_com_tarefa_de_checklist_nao_trava(admin):
    ags = {a["code"]: a for a in admin.get("/api/agents").json()["data"]}
    t = admin.post("/api/tasks", json={"title": "Criar um treinamento operacional de inventários", "template": "preventiva_ti",
                                       "assignee_type": "agente", "assignee_id": ags["coordenador"]["id"]}).json()["data"]
    for _ in range(2):                                   # antes: falhava a cada minuto com "Checklist incompleto"
        r = admin.post("/api/agents/coordenador/run").json()["data"]
        assert r["status"] == "concluido", r["summary"]
    d = admin.get(f"/api/tasks/{t['id']}").json()["data"]
    assert d["status"] == "aguardando_terceiro"
    assert any("precisa ser feito por uma pessoa" in c["body"] for c in d["comments"])
    assert R.db.val("SELECT COUNT(*) FROM agent_runs r JOIN agents a ON a.id=r.agent_id WHERE a.code='coordenador' AND r.status='falhou'") == 0


@pytest.fixture()
def chave(app):
    old = R.settings.anthropic_api_key
    brain._key_state.update(at=0.0, result=None)
    yield
    R.settings.anthropic_api_key = old
    brain.TRANSPORT.pop("key_check", None)
    brain._key_state.update(at=0.0, result=None)


def test_chave_sem_formato_recusada_e_aceita(chave, admin):
    R.settings.anthropic_api_key = ""
    assert brain.key_check()["nivel"] == "cinza"
    R.settings.anthropic_api_key = "abc123456789012345"
    assert brain.key_check()["nivel"] == "vermelho"                        # não é chave de API
    R.settings.anthropic_api_key = "sk-ant-api03-xxxxxxxxxxxxxxxxxxxxABCD"
    brain.TRANSPORT["key_check"] = lambda k: 401
    k = brain.key_check(force=True)
    assert k["nivel"] == "vermelho" and "recusada" in k["detalhe"] and k["chave"] == "sk-ant-…ABCD"
    assert "xxxxxxxx" not in str(k)                                        # chave nunca aparece inteira
    brain.TRANSPORT["key_check"] = lambda k: 200
    assert brain.key_check()["nivel"] == "vermelho"                        # cache de 1 h
    assert brain.key_check(force=True)["nivel"] == "verde"
    chk = next(c for c in admin.get("/api/saude?agora=1").json()["data"]["checks"] if c["code"] == "claude")
    assert chk["level"] == "verde" and "sk-ant-…ABCD" in chk["detail"]


def test_config_local_env_vence_variavel_antiga(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "config.local.env").write_text("﻿ANTHROPIC_API_KEY = 'sk-ant-nova-1234567890'\nexport OLLAMA_MODEL=qwen2.5:3b\nVAZIA=\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-antiga-do-windows")
    monkeypatch.delenv("VAZIA", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "temporario")   # garante restauração depois do teste
    config._FROM_FILE.clear(); config._OVERRIDDEN.clear()
    config._load_local_env()
    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-nova-1234567890"     # aspas simples e espaços removidos
    assert "ANTHROPIC_API_KEY" in config._OVERRIDDEN and os.environ["OLLAMA_MODEL"] == "qwen2.5:3b"
    assert "VAZIA" not in os.environ                                        # linha vazia não apaga nada
