"""Adaptadores de integrações externas.

- DemoPaymentProvider / DemoLogisticsProvider: SOMENTE no ambiente 'demo'. Todo registro criado por eles
  é marcado is_simulated=1 e a interface o identifica como simulação. Nenhum dinheiro ou envio real.
- Stripe / transportadoras / LLM: interfaces preparadas, aguardando credenciais e validação em sandbox.
"""
from __future__ import annotations

import json
import threading
import time

from .core import R, AppError, sign_payload
from .db import iso, new_id


class IntegrationPending(AppError):
    status = 503

    def __init__(self, name, what):
        super().__init__(f"Integração pendente: {name}. {what}", "integracao_pendente", extra={"integration": name})


TEST_CARDS = {
    "4242424242424242": "aprovado",
    "4000000000000002": "recusado",
}


class DemoPaymentProvider:
    code = "demo_pagamentos"
    simulated = True
    fee_bp = 349  # taxa fictícia de 3,49% usada apenas para exercitar a conciliação

    def __init__(self):
        if not R.settings.is_demo:
            raise IntegrationPending("pagamentos", "O provedor de demonstração não pode ser usado fora do ambiente demo.")

    def create_intent(self, order_id, amount_minor, currency):
        return f"demo_pi_{new_id('x')[2:]}"

    def confirm(self, provider_ref, amount_minor, currency, test_card):
        outcome = TEST_CARDS.get(test_card)
        if outcome is None:
            raise AppError("No ambiente de demonstração use apenas os cartões de teste listados.", "cartao_teste_invalido")
        fee = (amount_minor * self.fee_bp + 5000) // 10000
        if outcome == "aprovado":
            events = [
                ("payment.authorized", {"provider_ref": provider_ref, "amount_minor": amount_minor, "currency": currency}),
                ("payment.captured", {"provider_ref": provider_ref, "amount_minor": amount_minor, "currency": currency, "fee_minor": fee}),
            ]
        else:
            events = [("payment.failed", {"provider_ref": provider_ref, "reason": "cartao_recusado_teste"})]
        self.dispatch(events)
        return outcome

    def refund(self, provider_ref, amount_minor, currency, refund_id):
        ref = f"demo_re_{new_id('x')[2:]}"
        self.dispatch([("refund.succeeded", {"provider_ref": provider_ref, "refund_id": refund_id,
                                             "refund_ref": ref, "amount_minor": amount_minor, "currency": currency})])
        return ref

    # --- simulação de envio de webhooks assinados pelo "provedor"
    @staticmethod
    def build_webhook(type_, data, event_id=None, occurred_at=None):
        body = json.dumps({"id": event_id or new_id("evt"), "type": type_, "occurred_at": occurred_at or iso(),
                           "data": data}).encode()
        ts = int(time.time())
        sig = sign_payload(R.settings.webhook_secret_demo, ts, body)
        return body, {"x-demo-signature": f"t={ts},v1={sig}"}

    def dispatch(self, events):
        built = [self.build_webhook(t, d) for t, d in events]

        def run():
            from . import commerce
            for body, headers in built:
                try:
                    commerce.receive_webhook("demo", body, headers)
                except Exception:  # falhas ficam registradas na caixa de entrada para retentativa
                    pass

        delay = R.settings.demo_webhook_delay_s
        if delay <= 0:
            R.db.after_commit(run)
        else:
            R.db.after_commit(lambda: threading.Timer(delay, run).start())


class StripeProvider:
    code = "stripe"
    simulated = False

    def __init__(self):
        raise IntegrationPending("Stripe", "Defina STRIPE_SECRET_KEY e STRIPE_WEBHOOK_SECRET (modo teste) no servidor e "
                                 "conclua a validação da Etapa 3. Adaptador ainda não verificado.")


class DemoLogisticsProvider:
    code = "demo_logistica"
    simulated = True

    def __init__(self):
        if not R.settings.is_demo:
            raise IntegrationPending("logística", "Transportadora de demonstração indisponível fora do ambiente demo.")

    def register_shipment(self, order):
        return f"DEMO-{order['code']}"


def payment_provider(region):
    name = region.get("payment_provider") or ""
    if R.settings.is_demo:
        return DemoPaymentProvider()
    if name == "stripe":
        return StripeProvider()
    raise IntegrationPending("pagamentos", f"Nenhum provedor configurado para {region['code']}.")


def logistics_provider(region):
    if R.settings.is_demo:
        return DemoLogisticsProvider()
    raise IntegrationPending("logística", f"Nenhuma transportadora configurada para {region['code']}.")
