"""Identidade visual, site de alta conversão por país, captação de leads e funil.

- Marca (Cadastros → Identidade visual): nome, slogan, cores, fonte e tom — aplicados em todos os sites de país.
- Site: chamada clara, benefícios, "como funciona" em 3 passos, preço, perguntas, formulário de orçamento e botão de WhatsApp;
  sem scripts (CSP), leve e acessível; Open Graph para compartilhar; nada inventado (sem depoimentos/números falsos).
- Lead: formulário HTML puro (funciona sem JavaScript) com consentimento obrigatório (LGPD/GDPR), anti-spam (campo isca +
  limite por endereço), origem do anúncio (utm_*). Cada lead vira tarefa para a equipe; o evento não leva dados pessoais.
- Funil: visitas do site (contadas no servidor, sem cookies) → leads → ganhos.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import time
from datetime import timedelta

import base64

from .core import R, AppError, Conflict, NotFound, Principal, audit, emit
from .db import iso, new_id, now

BRAND_DEFAULT = {"nome": "", "slogan": "", "cor_primaria": "#0f766e", "cor_secundaria": "#1e3a8a", "cor_destaque": "#fbbf24",
                 "fonte": "system", "tom": "profissional e próximo", "sobre": "", "parceiros_ativo": False, "planos": []}
LAYOUTS = {"classico": "Clássico (conteúdo → catálogo → orçamento)", "vitrine": "Vitrine (catálogo com fotos em destaque)",
           "minimal": "Direto ao ponto (chamada, catálogo e orçamento)"}
FONTS = {"system": "system-ui,-apple-system,Segoe UI,Roboto,sans-serif", "serif": "Georgia,Cambria,'Times New Roman',serif",
         "rounded": "ui-rounded,'SF Pro Rounded','Nunito',system-ui,sans-serif", "mono": "ui-monospace,SFMono-Regular,Menlo,monospace"}
LEAD_STATUS = ("novo", "contatado", "proposta", "ganho", "perdido")
T = {  # textos do site por idioma (a IA escreve o conteúdo; isto é a moldura)
    "pt": {"catalog": "Catálogo", "ask_item": "Pedir orçamento deste item", "about": "Sobre nós", "partners": "Seja nosso parceiro", "partners_sub": "Opere com a nossa marca, sistema e suporte pagando uma mensalidade fixa.", "plan": "Plano", "month": "/mês", "partner_send": "Quero ser parceiro", "company": "Empresa", "bens": ["Atendimento ágil, sem sair do lugar", "Preço claro antes de começar", "Acompanhamento até resolver"], "benefits": "Por que nos escolher", "how": "Como funciona", "steps": ["Você conta o que precisa", "Fazemos um diagnóstico sem compromisso", "Executamos e acompanhamos"],
           "faq": "Perguntas frequentes", "form": "Peça seu orçamento", "name": "Nome", "email": "E-mail", "phone": "Telefone/WhatsApp", "msg": "Como podemos ajudar?",
           "consent": "Concordo em ser contatado sobre este pedido e com o uso dos meus dados para essa finalidade.", "send": "Enviar pedido",
           "thanks": "Recebemos seu pedido! Vamos responder em breve.", "error": "Confira os campos e o consentimento.", "wa": "Falar no WhatsApp"},
    "en": {"catalog": "Catalog", "ask_item": "Request a quote for this item", "about": "About us", "partners": "Become a partner", "partners_sub": "Operate with our brand, system and support for a fixed monthly fee.", "plan": "Plan", "month": "/month", "partner_send": "I want to be a partner", "company": "Company", "bens": ["Fast remote service", "Clear price before we start", "Follow-up until it is solved"], "benefits": "Why choose us", "how": "How it works", "steps": ["Tell us what you need", "We run a no-commitment assessment", "We deliver and follow up"],
           "faq": "Frequently asked questions", "form": "Request a quote", "name": "Name", "email": "Email", "phone": "Phone/WhatsApp", "msg": "How can we help?",
           "consent": "I agree to be contacted about this request and to the use of my data for this purpose.", "send": "Send request",
           "thanks": "We received your request and will reply soon.", "error": "Please check the fields and the consent box.", "wa": "Chat on WhatsApp"},
    "es": {"catalog": "Catálogo", "ask_item": "Pedir presupuesto de este ítem", "about": "Sobre nosotros", "partners": "Sé nuestro socio", "partners_sub": "Opera con nuestra marca, sistema y soporte pagando una cuota mensual fija.", "plan": "Plan", "month": "/mes", "partner_send": "Quiero ser socio", "company": "Empresa", "bens": ["Atención remota ágil", "Precio claro antes de empezar", "Seguimiento hasta resolver"], "benefits": "Por qué elegirnos", "how": "Cómo funciona", "steps": ["Nos cuentas lo que necesitas", "Hacemos un diagnóstico sin compromiso", "Ejecutamos y damos seguimiento"],
           "faq": "Preguntas frecuentes", "form": "Solicita tu presupuesto", "name": "Nombre", "email": "Correo", "phone": "Teléfono/WhatsApp", "msg": "¿Cómo podemos ayudarte?",
           "consent": "Acepto ser contactado sobre esta solicitud y el uso de mis datos para este fin.", "send": "Enviar solicitud",
           "thanks": "¡Recibimos tu solicitud! Responderemos pronto.", "error": "Revisa los campos y el consentimiento.", "wa": "Hablar por WhatsApp"},
    "fr": {"catalog": "Catalogue", "ask_item": "Demander un devis pour cet article", "about": "À propos", "partners": "Devenez partenaire", "partners_sub": "Opérez avec notre marque, système et support pour un abonnement mensuel fixe.", "plan": "Formule", "month": "/mois", "partner_send": "Je veux devenir partenaire", "company": "Entreprise", "bens": ["Service à distance rapide", "Prix clair avant de commencer", "Suivi jusqu'à la résolution"], "benefits": "Pourquoi nous choisir", "how": "Comment ça marche", "steps": ["Vous nous dites ce dont vous avez besoin", "Diagnostic sans engagement", "Nous réalisons et assurons le suivi"],
           "faq": "Questions fréquentes", "form": "Demandez un devis", "name": "Nom", "email": "E-mail", "phone": "Téléphone/WhatsApp", "msg": "Comment pouvons-nous aider ?",
           "consent": "J'accepte d'être contacté au sujet de cette demande et l'utilisation de mes données à cette fin.", "send": "Envoyer",
           "thanks": "Demande reçue ! Nous répondrons rapidement.", "error": "Vérifiez les champs et le consentement.", "wa": "Écrire sur WhatsApp"},
    "de": {"catalog": "Katalog", "ask_item": "Angebot für diesen Artikel", "about": "Über uns", "partners": "Partner werden", "partners_sub": "Arbeiten Sie mit unserer Marke, unserem System und Support gegen eine feste Monatsgebühr.", "plan": "Tarif", "month": "/Monat", "partner_send": "Partner werden", "company": "Firma", "bens": ["Schneller Remote-Service", "Klarer Preis vor dem Start", "Betreuung bis zur Lösung"], "benefits": "Warum wir", "how": "So funktioniert es", "steps": ["Sie schildern Ihren Bedarf", "Unverbindliche Analyse", "Umsetzung und Betreuung"],
           "faq": "Häufige Fragen", "form": "Angebot anfordern", "name": "Name", "email": "E-Mail", "phone": "Telefon/WhatsApp", "msg": "Wie können wir helfen?",
           "consent": "Ich bin einverstanden, zu dieser Anfrage kontaktiert zu werden, und mit der Nutzung meiner Daten zu diesem Zweck.", "send": "Anfrage senden",
           "thanks": "Anfrage erhalten! Wir melden uns in Kürze.", "error": "Bitte Felder und Einwilligung prüfen.", "wa": "Per WhatsApp schreiben"},
    "it": {"catalog": "Catalogo", "ask_item": "Richiedi preventivo per questo articolo", "about": "Chi siamo", "partners": "Diventa partner", "partners_sub": "Opera con il nostro marchio, sistema e supporto con un canone mensile fisso.", "plan": "Piano", "month": "/mese", "partner_send": "Voglio diventare partner", "company": "Azienda", "bens": ["Assistenza remota rapida", "Prezzo chiaro prima di iniziare", "Supporto fino alla soluzione"], "benefits": "Perché sceglierci", "how": "Come funziona", "steps": ["Ci racconti di cosa hai bisogno", "Analisi senza impegno", "Realizziamo e seguiamo"],
           "faq": "Domande frequenti", "form": "Richiedi un preventivo", "name": "Nome", "email": "E-mail", "phone": "Telefono/WhatsApp", "msg": "Come possiamo aiutarti?",
           "consent": "Accetto di essere contattato per questa richiesta e l'uso dei miei dati per questo scopo.", "send": "Invia richiesta",
           "thanks": "Richiesta ricevuta! Ti risponderemo presto.", "error": "Controlla i campi e il consenso.", "wa": "Scrivi su WhatsApp"},
}
_rate: dict = {}


# ------------------------------------------------------------------ identidade visual
def get_brand():
    row = R.db.one("SELECT value FROM app_settings WHERE key='marca'")
    return {**BRAND_DEFAULT, **(json.loads(row["value"]) if row else {})}


def save_brand(actor: Principal, data: dict):
    actor.require("mercados.write")
    b = get_brand()
    for k in ("nome", "slogan", "tom"):
        if k in data:
            b[k] = " ".join(str(data.get(k) or "").split())[:120]
    for k in ("cor_primaria", "cor_secundaria", "cor_destaque"):
        if k in data:
            v = str(data.get(k) or "").strip()
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", v):
                raise AppError(f"Cor inválida em {k} (use #RRGGBB)", "cor_invalida")
            b[k] = v.lower()
    if "sobre" in data:
        b["sobre"] = str(data.get("sobre") or "").strip()[:1500]
    if "parceiros_ativo" in data:
        b["parceiros_ativo"] = data.get("parceiros_ativo") in (True, "1", "on", "true", 1)
    if "planos" in data:
        pl = []
        for x in (data.get("planos") or [])[:3]:
            nome = " ".join(str(x.get("nome") or "").split())[:40]
            if not nome:
                continue
            pl.append({"nome": nome, "preco": " ".join(str(x.get("preco") or "").split())[:30],
                       "beneficios": [" ".join(str(y).split())[:100] for y in (x.get("beneficios") or []) if str(y).strip()][:6]})
        b["planos"] = pl
    if "fonte" in data:
        if data["fonte"] not in FONTS:
            raise AppError("Fonte inválida", "fonte_invalida")
        b["fonte"] = data["fonte"]
    with R.db.tx() as c:
        c.execute("INSERT OR REPLACE INTO app_settings(key,value,updated_at) VALUES ('marca',?,?)", (json.dumps(b, ensure_ascii=False), iso()))
        audit(c, actor, "marca.atualizada", "settings", "marca", None, b)
        emit(c, "marca.atualizada", "mercados")
    return b


# ------------------------------------------------------------------ visitas e leads
def count_view(market_id):
    day = now().strftime("%Y-%m-%d")
    with R.db.tx() as c:
        c.execute("INSERT INTO market_views(market_id,day,views) VALUES (?,?,1) ON CONFLICT(market_id,day) DO UPDATE SET views=views+1", (market_id, day))


def _clean(v, n):
    return " ".join(str(v or "").split())[:n] or None


def capture_lead(slug, form: dict, ip: str):
    """Retorna 'ok' | 'erro' | 'spam'. Nunca revela ao robô que foi descartado."""
    m = R.db.one("SELECT id, country, lang, site_published, config_json FROM markets WHERE slug=? AND status!='encerrado'", slug)
    if not m or not m["site_published"]:
        raise NotFound("Página não encontrada")
    if (form.get("website") or "").strip():          # campo isca: humanos não veem
        return "spam"
    t = time.time()
    hits = [x for x in _rate.get(ip, []) if t - x < 600]
    if len(hits) >= 5:
        return "spam"
    _rate[ip] = hits + [t]
    kind = "parceiro" if form.get("tipo") == "parceiro" else "cliente"
    plan = _clean(form.get("plano"), 40) if kind == "parceiro" else None
    name, email, phone = _clean(form.get("nome"), 120), _clean(form.get("email"), 160), _clean(form.get("telefone"), 40)
    msg = str(form.get("mensagem") or "").strip()[:2000] or None
    if not name or not (email or phone) or form.get("consentimento") not in ("1", "on", "sim", "true"):
        return "erro"
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return "erro"
    lid = new_id("lead")
    utm = {k: _clean(form.get(k), 80) for k in ("utm_source", "utm_medium", "utm_campaign")}
    with R.db.tx() as c:
        c.execute("INSERT INTO leads(id,market_id,name,email,phone,message,consent_at,utm_source,utm_medium,utm_campaign,lang,status,created_at,ip_hash,is_demo,kind,plan)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?,?,'novo',?,?,?,?,?)",
                  (lid, m["id"], name, email, phone, msg, iso(), utm["utm_source"], utm["utm_medium"], utm["utm_campaign"], m["lang"], iso(),
                   hashlib.sha256((ip + "|virtusx").encode()).hexdigest()[:16], 1 if R.settings.is_demo else 0, kind, plan))
        from . import tasks
        from .core import SYSTEM
        tasks.create_task(c, SYSTEM, {"title": (f"Parceiro interessado ({plan or 'plano a definir'}) · {m['country']} · {name}" if kind == "parceiro"
                                                else f"Novo lead · {m['country']} · {name}"), "priority": "alta", "source": "site",
                                      "related_entity": f"lead:{lid}", "due_at": iso(now() + timedelta(hours=24)),
                                      "description": "Responder em até 24 h (contato no painel do mercado → Leads)."})
        emit(c, "lead.novo", "site", "lead", lid, status="novo", payload={"pais": m["country"], "origem": utm["utm_source"] or "direto", "tipo": kind})
    return "ok"


def list_leads(actor: Principal, market_id=None):
    actor.require("leads.read")
    sql, args = "SELECT l.*, m.country FROM leads l JOIN markets m ON m.id=l.market_id", []
    if market_id:
        sql += " WHERE l.market_id=?"
        args.append(market_id)
    return R.db.all(sql + " ORDER BY l.created_at DESC LIMIT 500", *args)


def set_lead_status(actor: Principal, lid, status):
    actor.require("leads.write")
    if status not in LEAD_STATUS:
        raise AppError("Situação inválida", "status_invalido")
    with R.db.tx() as c:
        old = c.execute("SELECT status FROM leads WHERE id=?", (lid,)).fetchone()
        if not old:
            raise NotFound("Lead não encontrado")
        c.execute("UPDATE leads SET status=?, updated_at=? WHERE id=?", (status, iso(), lid))
        audit(c, actor, "lead.status", "lead", lid, {"status": old[0]}, {"status": status})
        emit(c, "lead.atualizado", "leads", "lead", lid, status=status)
    return {"id": lid, "status": status}


def funnel(market_id, days=30):
    since = (now() - timedelta(days=days)).strftime("%Y-%m-%d")
    views = R.db.val("SELECT COALESCE(SUM(views),0) FROM market_views WHERE market_id=? AND day>=?", market_id, since) or 0
    rows = R.db.all("SELECT status, COUNT(*) n FROM leads WHERE market_id=? AND created_at>=? GROUP BY status", market_id, since + "T00:00:00")
    by = {r["status"]: r["n"] for r in rows}
    leads = sum(by.values())
    src = R.db.all("SELECT COALESCE(utm_source,'direto') origem, COUNT(*) n FROM leads WHERE market_id=? AND created_at>=? GROUP BY 1 ORDER BY n DESC",
                   market_id, since + "T00:00:00")
    return {"dias": days, "visitas": views, "leads": leads, "ganhos": by.get("ganho", 0), "por_status": by,
            "conversao_visita_lead": round(100 * leads / views, 1) if views else None,
            "conversao_lead_ganho": round(100 * by.get("ganho", 0) / leads, 1) if leads else None, "origens": src}


# ------------------------------------------------------------------ catálogo / vitrine / layout
IMG_MAX = 2 * 1024 * 1024
_MAGIC = {b"\xff\xd8\xff": "jpg", b"\x89PNG\r\n\x1a\n": "png"}


def _img_dir():
    d = R.settings.data_dir / f"site_img_{R.settings.env}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _img_kind(b: bytes):
    for k, v in _MAGIC.items():
        if b.startswith(k):
            return v
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    return None


def _market(mid):
    m = R.db.one("SELECT id, slug, config_json FROM markets WHERE id=?", mid)
    if not m:
        raise NotFound("Mercado não encontrado")
    return m


def list_items(mid, only_active=False):
    rows = R.db.all("SELECT * FROM site_items WHERE market_id=?" + (" AND active=1" if only_active else "") + " ORDER BY position, created_at", mid)
    for r in rows:
        r["active"] = bool(r["active"])
    return rows


def save_item(actor: Principal, mid, d: dict, item_id=None):
    actor.require("mercados.write")
    _market(mid)
    title = " ".join(str(d.get("title") or "").split())[:80]
    if not title:
        raise AppError("Informe o nome do item", "titulo_obrigatorio")
    vals = {"title": title, "description": str(d.get("description") or "").strip()[:600] or None,
            "price": " ".join(str(d.get("price") or "").split())[:30] or None,
            "position": int(d.get("position") or 0), "active": 0 if d.get("active") in (False, 0, "0") else 1}
    photo = None
    if d.get("photo_b64"):
        try:
            raw = base64.b64decode(str(d["photo_b64"]).split(",")[-1], validate=True)
        except Exception:
            raise AppError("Foto inválida", "foto_invalida")
        if len(raw) > IMG_MAX:
            raise AppError("Foto acima de 2 MB", "foto_grande")
        kind = _img_kind(raw)
        if not kind:
            raise AppError("Use foto JPG, PNG ou WEBP", "foto_formato")
        photo = f"{hashlib.sha256(raw).hexdigest()[:24]}.{kind}"
        (_img_dir() / photo).write_bytes(raw)
    with R.db.tx() as c:
        if item_id:
            old = c.execute("SELECT * FROM site_items WHERE id=? AND market_id=?", (item_id, mid)).fetchone()
            if not old:
                raise NotFound("Item não encontrado")
            c.execute("UPDATE site_items SET title=?, description=?, price=?, position=?, active=?" + (", photo=?" if photo else "") + " WHERE id=?",
                      (*vals.values(), *([photo] if photo else []), item_id))
        else:
            if c.execute("SELECT COUNT(*) FROM site_items WHERE market_id=?", (mid,)).fetchone()[0] >= 60:
                raise Conflict("Limite de 60 itens por site", "limite_itens")
            item_id = new_id("item")
            c.execute("INSERT INTO site_items(id,market_id,title,description,price,photo,position,active,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (item_id, mid, vals["title"], vals["description"], vals["price"], photo, vals["position"], vals["active"], iso()))
        audit(c, actor, "site.item", "market", mid, None, {"item": item_id, "titulo": title, "foto": bool(photo)})
    return R.db.one("SELECT * FROM site_items WHERE id=?", item_id)


def delete_item(actor: Principal, mid, item_id):
    actor.require("mercados.write")
    with R.db.tx() as c:
        n = c.execute("DELETE FROM site_items WHERE id=? AND market_id=?", (item_id, mid)).rowcount
        if not n:
            raise NotFound("Item não encontrado")
        audit(c, actor, "site.item.remover", "market", mid, {"item": item_id}, None)
    return {"ok": True}


def set_layout(actor: Principal, mid, layout):
    actor.require("mercados.write")
    if layout not in LAYOUTS:
        raise AppError("Layout inválido", "layout_invalido")
    m = _market(mid)
    cfg = json.loads(m["config_json"] or "{}")
    old = cfg.get("layout", "classico")
    cfg["layout"] = layout
    with R.db.tx() as c:
        c.execute("UPDATE markets SET config_json=?, updated_at=? WHERE id=?", (json.dumps(cfg, ensure_ascii=False), iso(), mid))
        audit(c, actor, "site.layout", "market", mid, {"layout": old}, {"layout": layout})
    return {"layout": layout}


def image_path(slug, name):
    """Foto pública de item ativo de site publicado (ou None)."""
    if not re.fullmatch(r"[0-9a-f]{24}\.(jpg|png|webp)", name or ""):
        return None
    ok = R.db.val("SELECT 1 FROM site_items i JOIN markets m ON m.id=i.market_id WHERE m.slug=? AND i.photo=? LIMIT 1", slug, name)
    f = _img_dir() / name
    return f if ok and f.exists() else None


# ------------------------------------------------------------------ página
def _e(s):
    return html.escape(str(s or ""), quote=True)


def _desc(i):
    return f"<p>{_e(i['description'])}</p>" if i.get("description") else ""


def page(m: dict, base: str, others, published: bool, state: str = "", utm: dict | None = None):
    """HTML completo do site do país (sem scripts)."""
    s, b = m["site"], get_brand()
    lang2 = s["lang"].split("-")[0]
    t = T.get(lang2, T["en"])
    url = f"{base}/m/{m['slug']}"
    marca = b["nome"] or s["marca"]
    alts = "".join(f'<link rel="alternate" hreflang="{_e(o["lang"])}" href="{_e(base)}/m/{_e(o["slug"])}">' for o in others)
    ld = {"@context": "https://schema.org", "@type": "Organization", "name": marca, "url": url, "areaServed": s["pais"],
          "availableLanguage": s["lang"], "description": s["descricao"], **({"slogan": b["slogan"]} if b["slogan"] else {})}
    if s.get("email"):
        ld["contactPoint"] = {"@type": "ContactPoint", "email": s["email"], "contactType": "customer service"}
    faq_ld = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": f["p"], "acceptedAnswer": {"@type": "Answer", "text": f["r"]}} for f in s.get("faq") or [] if f.get("p")]}
    wa = re.sub(r"[^0-9]", "", s.get("whatsapp") or "")
    utm = utm or {}
    hidden = "".join(f'<input type="hidden" name="{k}" value="{_e(utm.get(k))}">' for k in ("utm_source", "utm_medium", "utm_campaign") if utm.get(k))
    msg = {"ok": f'<div class="note ok" role="status">{_e(t["thanks"])}</div>', "erro": f'<div class="note err" role="alert">{_e(t["error"])}</div>'}.get(state, "")
    price = (f'<div class="price"><span>{_e(s["a_partir"])}</span> <b>{_e(s["preco"])}</b> <small>{_e(s["moeda"])}</small></div>'
             if s.get("preco") else f'<div class="price"><small>{_e(s["precos"])}</small></div>')
    js = lambda d: json.dumps(d, ensure_ascii=False).replace("</", "<\\/")
    servicos = s["servicos"] if len(s.get("servicos") or []) >= 2 else t["bens"]          # sem IA: benefícios padrão do idioma (não repete a oferta)
    sobre = s["sobre"] if s.get("sobre") and s["sobre"] != s.get("chamada") else ""
    P, S2, A, F = b["cor_primaria"], b["cor_secundaria"], b["cor_destaque"], FONTS[b["fonte"]]
    layout = (m.get("config") or {}).get("layout", "classico")
    items = list_items(m["id"], only_active=True)
    item = next((i for i in items if i["id"] == (utm or {}).get("item")), None)
    pre = f"{t['ask_item']}: {item['title']}" if item else ""
    sl = _e(m["slug"])
    cards = "".join(
        f'<article class="card it">{f"""<img src="/m/{sl}/img/{_e(i["photo"])}" alt="{_e(i["title"])}" loading="lazy">""" if i["photo"] else ""}'
        f'<h3>{_e(i["title"])}</h3>{_desc(i)}'
        f'{f"""<p class="ip">{_e(i["price"])}</p>""" if i["price"] else ""}<a class="btn sm" href="?item={_e(i["id"])}#orcamento">{_e(t["ask_item"])}</a></article>'
        for i in items)
    catalog = f'<section class="w" id="catalogo"><h2>{_e(t["catalog"])}</h2><div class="grid cat">{cards}</div></section>' if items else ""
    about_txt = b.get("sobre") or ""
    about = f'<section class="w about" id="sobre"><h2>{_e(t["about"])}</h2><p>{_e(about_txt)}</p></section>' if about_txt else ""
    planos = b.get("planos") or []
    partners = ""
    if b.get("parceiros_ativo") and planos:
        pcards = "".join(
            f'<div class="card plan"><h3>{_e(x["nome"])}</h3>{f"""<p class="ip">{_e(x["preco"])}<small>{_e(t["month"])}</small></p>""" if x.get("preco") else ""}'
            f'<ul>{"".join(f"<li>{_e(y)}</li>" for y in x.get("beneficios") or [])}</ul></div>' for x in planos)
        opts = "".join(f'<option>{_e(x["nome"])}</option>' for x in planos)
        partners = (f'<section class="w" id="parceiros"><h2>{_e(t["partners"])}</h2><p>{_e(t["partners_sub"])}</p><div class="grid">{pcards}</div>'
                    f'<div class="card" style="max-width:640px;margin-top:18px"><form method="post" action="/m/{sl}/lead"><input type="hidden" name="tipo" value="parceiro">'
                    f'<div class="hp" aria-hidden="true"><label>Website<input name="website" tabindex="-1" autocomplete="off"></label></div>'
                    f'<label>{_e(t["plan"])}<select name="plano">{opts}</select></label>'
                    f'<label>{_e(t["name"])}<input name="nome" required maxlength="120" autocomplete="name"></label>'
                    f'<label>{_e(t["email"])}<input name="email" type="email" maxlength="160" autocomplete="email"></label>'
                    f'<label>{_e(t["phone"])}<input name="telefone" maxlength="40" autocomplete="tel"></label>'
                    f'<label>{_e(t["company"])}<textarea name="mensagem" rows="2" maxlength="2000"></textarea></label>'
                    f'<label class="chk"><input type="checkbox" name="consentimento" value="1" required> <span>{_e(t["consent"])}</span></label>'
                    f'<button class="btn" type="submit">{_e(t["partner_send"])}</button></form></div></section>')
    intro = f'<section class="w"><p style="font-size:18px">{_e(sobre)}</p></section>' if sobre else ''
    bens = f'<section class="w"><h2>{_e(t["benefits"])}</h2><div class="grid">' + "".join(f'<div class="card">✓ {_e(x)}</div>' for x in servicos) + '</div></section>'
    como = f'<section class="w"><h2>{_e(t["how"])}</h2><div class="grid">' + "".join(f'<div class="card step"><b>{i + 1}</b>{_e(x)}</div>' for i, x in enumerate(t["steps"])) + f'</div>{price}</section>'
    faq = (f'<section class="w"><h2>{_e(t["faq"])}</h2>' + "".join(f'<details><summary>{_e(f["p"])}</summary><p>{_e(f["r"])}</p></details>' for f in s.get("faq") or []) + '</section>') if s.get("faq") else ""
    body = {"vitrine": catalog + intro + bens + about + como + faq + partners,
            "minimal": catalog + price.join(['<section class="w">', '</section>']) + partners}.get(layout, intro + bens + como + catalog + about + faq + partners)
    return f"""<!DOCTYPE html><html lang="{_e(s['lang'])}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(s['titulo'])}</title><meta name="description" content="{_e(s['descricao'])}"><meta name="theme-color" content="{P}">
<link rel="canonical" href="{_e(url)}">{alts}{'' if published else '<meta name="robots" content="noindex">'}
<meta property="og:type" content="website"><meta property="og:title" content="{_e(s['titulo'])}"><meta property="og:description" content="{_e(s['descricao'])}">
<meta property="og:url" content="{_e(url)}"><meta property="og:locale" content="{_e(s['lang'].replace('-', '_'))}"><meta name="twitter:card" content="summary">
<script type="application/ld+json">{js(ld)}</script>{f'<script type="application/ld+json">{js(faq_ld)}</script>' if faq_ld["mainEntity"] else ''}
<style>:root{{--p:{P};--s:{S2};--a:{A}}}*{{box-sizing:border-box}}body{{margin:0;font-family:{F};color:#0f172a;background:#f8fafc;line-height:1.55}}
a{{color:var(--p)}}.w{{max-width:1040px;margin:auto;padding:0 20px}}.pv{{background:#b91c1c;color:#fff;text-align:center;padding:6px;font-size:14px}}
nav{{display:flex;justify-content:space-between;align-items:center;padding:16px 0}}.logo{{font-weight:800;font-size:20px;color:#fff;text-decoration:none}}
.hero{{background:linear-gradient(135deg,var(--p),var(--s));color:#fff;padding-bottom:56px}}.hero h1{{font-size:clamp(28px,5vw,48px);line-height:1.12;margin:18px 0 10px;max-width:760px}}
.hero p{{font-size:19px;opacity:.95;max-width:680px}}.kick{{display:inline-block;background:rgba(255,255,255,.16);padding:4px 10px;border-radius:99px;font-size:13px}}
.btn{{display:inline-block;background:var(--a);color:#111;font-weight:700;padding:14px 22px;border-radius:12px;text-decoration:none;border:0;font-size:16px;cursor:pointer}}
.btn.alt{{background:transparent;color:#fff;border:2px solid rgba(255,255,255,.7);margin-left:8px}}section{{padding:48px 0}}h2{{font-size:28px;margin:0 0 18px}}
.grid{{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}}.card{{background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:18px;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
.step b{{display:inline-grid;place-items:center;width:32px;height:32px;border-radius:50%;background:var(--p);color:#fff;margin-right:8px}}
.price{{font-size:22px;margin:14px 0}}.price b{{font-size:34px}}details{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:12px 16px;margin:8px 0}}
summary{{cursor:pointer;font-weight:600}}form{{display:grid;gap:12px}}label{{font-weight:600;font-size:14px}}input,textarea{{width:100%;padding:12px;border:1px solid #cbd5e1;border-radius:10px;font:inherit}}
.chk{{display:flex;gap:10px;align-items:flex-start;font-weight:400}}.chk input{{width:auto;margin-top:4px}}.hp{{position:absolute;left:-5000px}}
.note{{padding:12px 14px;border-radius:10px;margin-bottom:12px}}.ok{{background:#dcfce7;color:#14532d}}.err{{background:#fee2e2;color:#7f1d1d}}
.wa{{position:fixed;right:18px;bottom:18px;background:#25d366;color:#fff;font-weight:700;padding:14px 18px;border-radius:99px;text-decoration:none;box-shadow:0 8px 24px rgba(0,0,0,.25)}}
select{{width:100%;padding:12px;border:1px solid #cbd5e1;border-radius:10px;font:inherit}}.btn.sm{{padding:9px 14px;font-size:14px}}
.it img{{width:100%;aspect-ratio:4/3;object-fit:cover;border-radius:10px;margin-bottom:8px}}.it h3,.plan h3{{margin:4px 0}}.ip{{font-weight:800;font-size:20px;color:var(--p)}}
.plan ul{{padding-left:18px}}.about p{{font-size:18px;max-width:780px}}.cat{{grid-template-columns:repeat(auto-fill,minmax(240px,1fr))}}.lay-vitrine .cat{{grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}}
.lay-minimal .hero{{padding-bottom:28px}}.navl a{{color:#fff;margin-right:14px;text-decoration:none;font-weight:600}}
footer{{background:#0f172a;color:#cbd5e1;padding:28px 0;font-size:14px}}footer a{{color:#fff}}@media(max-width:600px){{.btn.alt{{margin:10px 0 0}}}}</style></head><body class="lay-{_e(layout)}">
{'' if published else '<div class="pv">PRÉVIA — não publicado</div>'}
<header class="hero"><div class="w"><nav><a class="logo" href="#">{_e(marca)}</a><span class="navl">{f'<a href="#catalogo">{_e(t["catalog"])}</a>' if items else ''}{f'<a href="#sobre">{_e(t["about"])}</a>' if about else ''}{f'<a href="#parceiros">{_e(t["partners"])}</a>' if partners else ''}<a class="btn" href="#orcamento">{_e(s['cta'])}</a></span></nav>
<span class="kick">{_e(s['remoto'])}</span><h1>{_e(s['titulo'])}</h1><p>{_e(s['chamada'])}</p>{f'<p><i>{_e(b["slogan"])}</i></p>' if b['slogan'] else ''}
<a class="btn" href="#orcamento">{_e(t['form'])}</a>{f'<a class="btn alt" href="https://wa.me/{wa}">{_e(t["wa"])}</a>' if wa else ''}</div></header>
<main>{body}
<section class="w" id="orcamento"><div class="card" style="max-width:640px"><h2>{_e(t['form'])}</h2>{msg}
<form method="post" action="/m/{_e(m['slug'])}/lead">{hidden}<div class="hp" aria-hidden="true"><label>Website<input name="website" tabindex="-1" autocomplete="off"></label></div>
<label>{_e(t['name'])}<input name="nome" required maxlength="120" autocomplete="name"></label>
<label>{_e(t['email'])}<input name="email" type="email" maxlength="160" autocomplete="email"></label>
<label>{_e(t['phone'])}<input name="telefone" maxlength="40" autocomplete="tel"></label>
<label>{_e(t['msg'])}<textarea name="mensagem" rows="4" maxlength="2000">{_e(pre)}</textarea></label>
<label class="chk"><input type="checkbox" name="consentimento" value="1" required> <span>{_e(t['consent'])}</span></label>
<button class="btn" type="submit">{_e(t['send'])}</button></form></div></section></main>
<footer><div class="w"><b>{_e(marca)}</b> · {_e(s['remoto'])}{f' · <a href="mailto:{_e(s["email"])}">{_e(s["email"])}</a>' if s.get('email') else ''}</div></footer>
{f'<a class="wa" href="https://wa.me/{wa}" aria-label="WhatsApp">WhatsApp</a>' if wa else ''}</body></html>"""
