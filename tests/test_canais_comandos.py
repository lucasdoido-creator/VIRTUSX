"""WhatsApp (webhook assinado, deduplicação, janela de 24 h, envio pela API oficial), canais internos e central de comandos."""
import hashlib
import hmac
import json

import pytest

from app import channels, db as dbmod
from app.core import R

SECRET = "segredo-do-app"


def payload(msg_id, frm="5511912345678", text="Olá, a impressora parou", name="Paula"):
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": "PNID"},
        "contacts": [{"wa_id": frm, "profile": {"name": name}}],
        "messages": [{"from": frm, "id": msg_id, "timestamp": "1794280000", "type": "text", "text": {"body": text}}]}}]}]}


def signed(client, data):
    raw = json.dumps(data).encode()
    sig = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/api/webhooks/whatsapp", content=raw, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})


@pytest.fixture()
def wa(app):
    s = R.settings
    s.whatsapp_token, s.whatsapp_phone_number_id, s.whatsapp_app_secret, s.whatsapp_verify_token = "tok", "PNID", SECRET, "verifica"
    sent = []
    channels.TRANSPORT["send"] = lambda p: sent.append(p) or {"messages": [{"id": f"wamid.out{len(sent)}"}]}
    yield sent
    channels.TRANSPORT["send"] = None
    s.whatsapp_token = s.whatsapp_phone_number_id = s.whatsapp_app_secret = s.whatsapp_verify_token = ""


def test_verificacao_do_webhook(wa, public):
    r = public.get("/api/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=verifica&hub.challenge=12345")
    assert r.status_code == 200 and r.text == "12345"
    assert public.get("/api/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=errado&hub.challenge=1").status_code == 403


def test_recebe_mensagem_assinada_deduplica_e_vincula_cliente(wa, public, admin):
    cid = admin.post("/api/cadastros/clientes", json={"name": "Clínica Vida"}).json()["data"]["id"]
    admin.post("/api/contacts", json={"partner_id": cid, "name": "Dra. Paula", "phone": "(11) 91234-5678"})
    assert public.post("/api/webhooks/whatsapp", content=json.dumps(payload("wamid.1")).encode(),
                       headers={"X-Hub-Signature-256": "sha256=errada"}).status_code == 403
    assert signed(public, payload("wamid.1")).json()["data"]["mensagens"] == 1
    assert signed(public, payload("wamid.1")).json()["data"]["duplicadas"] == 1  # reentrega do Meta
    convs = admin.get("/api/conversations").json()["data"]
    assert len(convs) == 1 and convs[0]["partner_id"] == cid and convs[0]["unread"] == 1 and convs[0]["window_open"]
    ev = R.db.one("SELECT * FROM events WHERE type='whatsapp.mensagem_recebida'")
    assert ev["partner_id"] == cid and "impressora" not in ev["payload_json"]  # conteúdo não vai para eventos


def test_envio_dentro_da_janela_e_status(wa, public, admin):
    signed(public, payload("wamid.2"))
    conv = admin.get("/api/conversations").json()["data"][0]
    r = admin.post(f"/api/conversations/{conv['id']}/send", json={"text": "Já estamos verificando!"})
    assert r.status_code == 200 and r.json()["data"]["simulated"] is False
    assert wa[0]["to"] == "5511912345678" and wa[0]["text"]["body"] == "Já estamos verificando!" and wa[0]["messaging_product"] == "whatsapp"
    st = {"entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.out1", "status": "read"}]}}]}]}
    signed(public, st)
    assert R.db.val("SELECT status FROM messages WHERE wa_message_id='wamid.out1'") == "lida"


def test_fora_da_janela_de_24h_bloqueia(wa, public, admin):
    signed(public, payload("wamid.3"))
    conv = admin.get("/api/conversations").json()["data"][0]
    with R.db.tx() as c:  # última mensagem do cliente há 25 h
        c.execute("UPDATE conversations SET last_inbound_at=?", (dbmod.iso(dbmod.now() - __import__("datetime").timedelta(hours=25)),))
    r = admin.post(f"/api/conversations/{conv['id']}/send", json={"text": "oi"})
    assert r.status_code == 409 and r.json()["error"] == "fora_da_janela" and wa == []


def test_numero_desconhecido_vincula_e_abre_chamado(wa, public, admin):
    signed(public, payload("wamid.4", frm="5519988887777", text="Sem internet na filial"))
    conv = admin.get("/api/conversations").json()["data"][0]
    assert conv["partner_id"] is None
    assert admin.post(f"/api/conversations/{conv['id']}/ticket", json={}).json()["error"] == "sem_cliente"
    cid = admin.post("/api/cadastros/clientes", json={"name": "Escritório Alfa"}).json()["data"]["id"]
    admin.post(f"/api/conversations/{conv['id']}/link", json={"partner_id": cid, "name": "Recepção Alfa"})
    t = admin.post(f"/api/conversations/{conv['id']}/ticket", json={}).json()["data"]["task_id"]
    task = admin.get(f"/api/tasks/{t}").json()["data"]
    assert task["template"] == "chamado_ti" and "Sem internet na filial" in task["description"] and task["partner_id"] == cid


def test_sem_configuracao_producao_recusa_e_demo_simula(app, admin, public):
    # sem credenciais: webhook recusa; no demo, a simulação é marcada como simulada
    assert public.get("/api/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=x&hub.challenge=1").status_code == 403
    r = admin.post("/api/demo/whatsapp-inbound", json={"phone": "11 98888-7777", "text": "Teste de atendimento"})
    assert r.json()["data"]["mensagens"] == 1
    conv = admin.get("/api/conversations").json()["data"][0]
    out = admin.post(f"/api/conversations/{conv['id']}/send", json={"text": "Resposta de teste"}).json()["data"]
    assert out["simulated"] is True
    assert R.db.val("SELECT is_simulated FROM messages WHERE direction='in'") == 1


def test_parceiro_ve_so_as_proprias_conversas(app, admin, marina, joao):
    admin.post("/api/contacts", json={"partner_id": "prt_aurora", "name": "Marina", "phone": "11 97777-6666"})
    admin.post("/api/demo/whatsapp-inbound", json={"phone": "11 97777-6666", "text": "Oi"})
    admin.post("/api/demo/whatsapp-inbound", json={"phone": "11 95555-4444", "text": "Número desconhecido"})
    assert len(marina.get("/api/conversations").json()["data"]) == 1
    assert joao.get("/api/conversations").json()["data"] == []
    cid = marina.get("/api/conversations").json()["data"][0]["id"]
    assert marina.post(f"/api/conversations/{cid}/send", json={"text": "x"}).status_code == 403  # parceiro só lê
    other = [c for c in admin.get("/api/conversations").json()["data"] if c["partner_id"] is None][0]
    assert marina.get(f"/api/conversations/{other['id']}").status_code == 404


def test_canal_interno_da_equipe(app, admin):
    ch = admin.post("/api/channels", json={"name": "Equipe Base Centro"}).json()["data"]["id"]
    conv = [c for c in admin.get("/api/conversations").json()["data"] if c["channel_id"] == ch][0]
    assert admin.post(f"/api/conversations/{conv['id']}/send", json={"text": "Reunião às 9h"}).json()["data"]["status"] == "registrada"
    assert admin.get("/api/channels").json()["data"]["whatsapp"]["missing"]


# ------------------------------------------------------------------ comandos
def test_comandos_regras_e_execucao(app, admin):
    cid = admin.post("/api/cadastros/clientes", json={"name": "Clínica Vida"}).json()["data"]["id"]
    admin.post("/api/cadastros/unidades", json={"client_id": cid, "name": "Matriz", "city": "São Paulo", "state": "SP"})
    p = admin.post("/api/commands/parse", json={"text": "chamado Clínica Vida unidade Matriz prazo amanhã: impressora não imprime"}).json()["data"]
    assert p["action"] == "criar_tarefa" and p["requires_confirmation"] and "Matriz" in p["summary"]
    r = admin.post("/api/commands/execute", json={"action": p["action"], "args": p["args"]}).json()["data"]
    t = admin.get(f"/api/tasks/{r['result']['task_id']}").json()["data"]
    assert t["template"] == "chamado_ti" and t["description"] == "impressora não imprime" and t["unit_id"] and t["due_at"]
    p = admin.post("/api/commands/parse", json={"text": "ideia Clínica Vida: backup em nuvem"}).json()["data"]
    assert p["action"] == "nova_ideia"
    assert admin.post("/api/commands/parse", json={"text": "rodar agente pesquisa"}).json()["data"]["args"] == {"code": "pesquisa"}
    assert admin.post("/api/commands/parse", json={"text": "pausar agentes"}).json()["data"]["action"] == "pausar_agentes"
    assert admin.post("/api/commands/parse", json={"text": "abrir Brasil"}).json()["data"]["requires_confirmation"] is False
    f = admin.post("/api/commands/parse", json={"text": "fechamento Clínica Vida 11/2026"}).json()["data"]
    assert f["args"]["local_day"] == "2026-11-01"
    bad = admin.post("/api/commands/parse", json={"text": "faça mágica"})
    assert bad.status_code == 400 and bad.json()["error"] == "comando_desconhecido"
    assert admin.post("/api/commands/parse", json={"text": "chamado Cliente Inexistente: x"}).json()["error"] == "cliente_ausente"
    assert admin.post("/api/commands/execute", json={"action": "apagar_tudo", "args": {}}).status_code == 400


def test_comando_respeita_permissoes(app, marina):
    p = marina.post("/api/commands/parse", json={"text": "tarefa Casa Aurora: conferir vitrine"}).json()["data"]
    assert marina.post("/api/commands/execute", json={"action": p["action"], "args": p["args"]}).status_code == 200
    assert marina.post("/api/commands/parse", json={"text": "tarefa Lisboa Design Store: x"}).json()["error"] == "cliente_ausente"
    assert marina.post("/api/commands/execute", json={"action": "pausar_agentes", "args": {}}).status_code == 403


def test_comando_por_ia_gratis_exige_confirmacao(app, admin):
    from app import brain
    brain.TRANSPORT["ollama"] = lambda payload: {"message": {"content": json.dumps({"comando": "pausar agentes"})}}
    brain._ollama_state.update(checked_at=1e18, ok=True)
    try:
        p = admin.post("/api/commands/parse", json={"text": "por favor dá um tempo em todos os robôs"}).json()["data"]
        assert p["source"] == "ia" and p["action"] == "pausar_agentes" and p["requires_confirmation"]
    finally:
        brain.TRANSPORT["ollama"] = None
        brain._ollama_state.update(checked_at=0.0, ok=False)
