// Página pública da loja do parceiro (template personalizável) com checkout de teste.
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const slug = decodeURIComponent(location.pathname.split("/").pop());
let store, cart = {}, money;
const STATES = { aguardando_pagamento: "Aguardando confirmação do pagamento", pago: "Pagamento confirmado", enviado: "Pedido enviado",
  pagamento_recusado: "Pagamento recusado", cancelado: "Pedido cancelado", expirado: "Reserva expirada", devolvido: "Devolvido" };

async function j(url, opts) { const r = await fetch(url, opts); const d = await r.json(); if (!d.ok) { const e = new Error(d.message); e.code = d.error; throw e; } return d.data; }

async function init() {
  try { store = await j(`/api/public/stores/${encodeURIComponent(slug)}`); }
  catch (e) { $("#root").innerHTML = `<p>${esc(e.message)}</p>`; return; }
  money = v => new Intl.NumberFormat(store.region.locale, { style: "currency", currency: store.region.currency }).format(v / 100);
  document.title = store.title;
  document.documentElement.style.setProperty("--accent", store.config.accent);
  const eb = $("#envbar"); eb.className = "envbar " + store.environment;
  eb.textContent = store.environment === "demo" ? "LOJA DE DEMONSTRAÇÃO · use apenas os cartões de teste · nenhuma cobrança real" : store.environment === "sandbox" ? "AMBIENTE DE TESTES" : "";
  const saved = sessionStorage.getItem("pedido:" + slug);
  if (saved) { const s = JSON.parse(saved); return showStatus(s.code, s.token); }
  render();
}

function render() {
  const s = store, list = s.config.layout === "lista" ? "lista" : "";
  $("#root").innerHTML = `
    <header class="hero"><h1>${esc(s.title)}</h1><p>${esc(s.config.headline || "")}</p>
      <div class="seller"><b>Vendido e faturado por:</b> ${esc(s.seller.seller_of_record)}<br><b>Loja parceira:</b> ${esc(s.seller.referral_partner)} (recebe comissão sobre a venda)</div>
      ${s.region.sales_enabled ? "" : `<p class="err">Vendas indisponíveis em ${esc(s.region.name)}: ${esc(s.region.sales_status_note || "")}</p>`}</header>
    <div class="layout"><section class="grid ${list}">${s.products.map(p => `<article class="prod"><div class="ph" aria-hidden="true"></div>
      <h3>${esc(p.name)}</h3><div class="d">${esc(p.description || "")}</div><div class="price">${money(p.price_minor)}</div>
      ${p.available ? `<button class="btn ghost" data-add="${p.id}">Adicionar</button>` : `<button class="btn ghost" disabled>Sem estoque</button>`}</article>`).join("")}</section>
    <aside class="cart" aria-label="Carrinho"><h2>Carrinho</h2><div id="lines"></div>
      <form class="f" id="co"><label>Nome<input name="name" required autocomplete="name"></label><label>E-mail<input name="email" type="email" required autocomplete="email"></label>
      <label>Endereço<input name="address" required autocomplete="street-address"></label><label>Cidade<input name="city" required></label>
      <label>Estado/Distrito<input name="state"></label><label>Código postal<input name="postal_code" required></label>
      ${s.test_cards.length ? `<label>Cartão de teste<select name="card">${s.test_cards.map(([n, o]) => `<option value="${n}">${n.replace(/(\d{4})/g, "$1 ").trim()} — ${o}</option>`).join("")}</select></label>
        <div class="note">Ambiente de demonstração: o pagamento é simulado por um provedor de teste que envia webhooks assinados. Não digite dados de cartão reais.</div>` : `<div class="note">Pagamento real indisponível nesta etapa.</div>`}
      <button class="btn" id="buy" ${s.region.sales_enabled ? "" : "disabled"}>Finalizar pedido de teste</button><div class="err" id="err" role="alert"></div></form></aside></div>`;
  document.querySelectorAll("[data-add]").forEach(b => b.onclick = () => { cart[b.dataset.add] = (cart[b.dataset.add] || 0) + 1; lines(); });
  $("#co").onsubmit = checkout; lines();
}

function lines() {
  const items = Object.entries(cart).filter(([, q]) => q > 0);
  const sub = items.reduce((t, [id, q]) => t + store.products.find(p => p.id === id).price_minor * q, 0);
  $("#lines").innerHTML = items.length ? items.map(([id, q]) => { const p = store.products.find(x => x.id === id);
    return `<div style="display:flex;justify-content:space-between;gap:8px;margin:4px 0"><span>${esc(p.name)}</span><input class="qty" type="number" min="0" max="20" value="${q}" data-q="${id}" aria-label="Quantidade"></div>`; }).join("") +
    `<p>Subtotal ${money(sub)} · Frete ${money(store.shipping_minor)}<br><b>Total ${money(sub + store.shipping_minor)}</b></p>` : `<p class="note">Vazio.</p>`;
  document.querySelectorAll("[data-q]").forEach(i => i.onchange = () => { cart[i.dataset.q] = Math.max(0, Math.min(20, +i.value || 0)); lines(); });
}

async function checkout(e) {
  e.preventDefault(); const f = new FormData(e.target); const items = Object.entries(cart).filter(([, q]) => q > 0).map(([product_id, qty]) => ({ product_id, qty }));
  if (!items.length) { $("#err").textContent = "Adicione produtos ao carrinho."; return; }
  $("#buy").disabled = true; $("#err").textContent = "";
  // Chave de idempotência por tentativa: um duplo clique ou reenvio não cria dois pedidos.
  const key = sessionStorage.getItem("idem:" + slug) || crypto.randomUUID(); sessionStorage.setItem("idem:" + slug, key);
  try {
    const o = await j(`/api/public/stores/${encodeURIComponent(slug)}/checkout`, { method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key },
      body: JSON.stringify({ items, customer: { name: f.get("name"), email: f.get("email") },
        shipping: { country: store.region.code, address: f.get("address"), city: f.get("city"), state: f.get("state"), postal_code: f.get("postal_code") } }) });
    if (o.duplicate && !o.access_token) { $("#err").textContent = "Este pedido já foi enviado."; return; }
    sessionStorage.removeItem("idem:" + slug);
    sessionStorage.setItem("pedido:" + slug, JSON.stringify({ code: o.order_code, token: o.access_token }));
    await j(`/api/public/orders/${o.order_code}/pay`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: o.access_token, test_card: f.get("card") }) });
    showStatus(o.order_code, o.access_token);
  } catch (er) { $("#err").textContent = er.message; $("#buy").disabled = false; /* erro de negócio: a próxima tentativa usa nova chave */ if (er.code) sessionStorage.removeItem("idem:" + slug); }
}

async function showStatus(code, token) {
  let o; try { o = await j(`/api/public/orders/${code}?token=${encodeURIComponent(token)}`); } catch { sessionStorage.removeItem("pedido:" + slug); return render(); }
  $("#root").innerHTML = `<div class="status"><div class="note">Pedido ${esc(o.code)} · vendido por ${esc(o.seller_of_record)}</div>
    <div class="big">${esc(STATES[o.status] || o.status)}</div>
    <p>Total ${money(o.total_minor)} · pagamento: ${esc(o.payment?.status || "—")} ${o.payment?.is_simulated ? "(simulado)" : ""}
    ${o.shipment ? `<br>Envio: ${esc(o.shipment.tracking_code)} ${o.shipment.is_simulated ? "(simulado)" : ""}` : ""}</p>
    <ul>${o.items.map(i => `<li>${i.qty}× ${esc(i.name)} — ${money(i.line_total_minor)}</li>`).join("")}</ul>
    <button class="btn ghost" id="again">Fazer outro pedido de teste</button></div>`;
  $("#again").onclick = () => { sessionStorage.removeItem("pedido:" + slug); cart = {}; render(); };
  if (o.status === "aguardando_pagamento" || o.status === "pago") setTimeout(() => showStatus(code, token), 2000);
}
init();
