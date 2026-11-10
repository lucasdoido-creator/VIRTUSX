# VIRTUSX — documentação única

> Fonte única de verdade do projeto. Atualize este arquivo a cada etapa: decisões, arquitetura, modelo de dados, etapas, integrações, testes, limitações e custos.
> Última atualização: 10/11/2026 · Etapa 1 concluída · Etapa 2 em versão inicial · **CEO de IA por parceiro** · Windows · CI no GitHub.

---

## 1. Visão do produto

Empresa central que controla catálogo, produtos, tecnologia e operação comercial, com bases regionais (**Brasil, Portugal, Estados Unidos**). Parceiros acessam um painel, selecionam produtos autorizados, personalizam sua página e acompanham vendas, comissões, tarefas e fechamentos. Um centro de operações em 3D (com equivalente completo em tabelas) mostra regiões, centros de distribuição, prédios dos parceiros, setores da central e agentes operacionais — sempre a partir de dados e eventos reais do sistema.

Modelos comerciais:

| Modelo | Quem fatura o cliente | Remuneração do parceiro | Situação |
|---|---|---|---|
| `central_commission` — venda pela central | Entidade vendedora da região (ex.: *Central Brasil Comércio Ltda.*) | Comissão sobre produtos (sem frete) | **Operacional (demo)** |
| `b2b_resale` — revenda independente | O próprio parceiro | Margem entre preço B2B e preço ao consumidor | Modelado (`b2b_price_minor`, `commercial_model`), **checkout recusa** com `modalidade_nao_operacional` |

Os dois modelos não compartilham lançamentos: comissão só é apurada quando pedido **e** parceiro são `central_commission`.

## 2. Decisões tomadas

| # | Decisão | Motivo | Revisar quando |
|---|---|---|---|
| D1 | Python + Starlette + SQLite (WAL), SQL portável | Sem acesso a rede para instalar pacotes; SQLite atende o piloto com um processo | Antes da Etapa 4: migrar para PostgreSQL gerenciado |
| D2 | Renderizador WebGL próprio (`web/js/gl.js`) no lugar de Three.js | Three.js não pôde ser baixado neste ambiente; o requisito permite "tecnologia equivalente". Cena é declarativa (`world.js`), facilitando troca | Ao habilitar build front-end com npm |
| D3 | GSAP opcional (`web/vendor/gsap.min.js`); sem ele, interpolador local com a mesma API | Mesmo motivo de D2 | Ao adicionar o arquivo |
| D4 | Um banco por ambiente (`plataforma_demo.sqlite3`, `_sandbox`, `_production`) + marca `meta.environment` que impede abrir banco de outro ambiente | Nunca misturar dados fictícios e reais | — |
| D5 | Provedores de pagamento/logística de demonstração **só** em `APP_ENV=demo`; registros marcados `is_simulated` | Não simular sucesso fora da demonstração | — |
| D6 | Comissão: base = subtotal de produtos (sem frete), arredondamento meio-para-cima em centavos, apurada **no registro do envio**, em carência até o fim do prazo de devolução da região, estornada em reembolso | Deterministicamente testável | **Decisão comercial pendente** (base, momento e prazo) |
| D7 | Pagamento confirmado após cancelamento **não reativa** o pedido: gera pendência e reembolso sob política | Consistência com eventos fora de ordem | — |
| D8 | Sem política cadastrada → ação preparada para revisão | Requisito de autonomia | — |
| D9 | Sessão por cookie HttpOnly + SameSite=Strict + cabeçalho anti-CSRF; senha scrypt | Segurança sem dependências externas | Adicionar 2FA na Etapa 4 |
| D10 | Tempo real por SSE com `Last-Event-ID` sobre tabela `events` persistente | Recuperação após desconexão sem perder eventos | Escala multi-instância: trocar polling do banco por LISTEN/NOTIFY |
| D11 | Impostos **não calculados** em nenhuma região; preço exibido é o preço cadastrado | Regras fiscais (ICMS/ST/NF-e, IVA, sales tax) exigem decisão contábil | Etapa 3 |
| D12 | Vendas liberadas por país via `regions.sales_enabled`; EUA bloqueado na demonstração; entrega só no país da loja | "Não liberar automaticamente para o mundo" | — |

## 3. Arquitetura

```
 Navegador (central / parceiro)          Loja pública (/loja/<slug>)
   ├─ index.html + app.js (painéis, tabelas, prédio do parceiro)    └─ loja.js (checkout de teste)
   ├─ gl.js (WebGL) + world.js (cidade)  ─ anim.js (GSAP ou local)
   └─ api.js: REST + SSE (/api/stream, retomada por Last-Event-ID)
                │
 Servidor Starlette (server/app)
   ├─ main.py        rotas, sessão, CSRF, cabeçalhos de segurança, SSE, agendador
   ├─ core.py        permissões (RBAC + isolamento por parceiro), eventos, auditoria, mascaramento de PII
   ├─ commerce.py    vitrines, checkout idempotente, reservas, webhooks (HMAC, deduplicação, ordem, retentativa, fila de falhas),
   │                 envio, comissão, cancelamento, devolução, reembolso
   ├─ policies.py    políticas por ação, solicitações, aprovação com segregação de funções, cancelamento ≠ desfazer
   ├─ tasks.py       tarefas/pendências, dependências, recorrência no servidor, anexos com SHA-256, histórico
   ├─ closings.py    fechamentos vendas/comissões/conciliação, fusos, revisões, liquidação separada
   ├─ agents.py      agentes, execuções, registros de ferramentas, custos, pausa global
   ├─ ops.py         sinalização dos prédios, mundo 3D, prédio do parceiro, central de aplicativos
   ├─ providers.py   adaptadores (demo pagamentos/logística; Stripe e transportadoras pendentes)
   └─ db.py          esquema, transações serializadas, relógio testável
 Agendador (thread no servidor, independe do painel aberto): heartbeat de módulos internos, recorrências,
   retentativas de webhook, expiração de reservas, liberação de comissões, agentes devidos.
```

**Segurança e dados**
- Credenciais apenas em variáveis de ambiente do servidor (`STRIPE_SECRET_KEY`, `ANTHROPIC_API_KEY`, `DEMO_WEBHOOK_SECRET`); o segredo de webhook demo é gerado por instalação em `data/`.
- Webhooks: assinatura HMAC-SHA256 com carimbo de tempo (tolerância 5 min), caixa de entrada única por `(provedor, id_evento)`.
- Eventos e registros de agentes não carregam dados pessoais (`scrub()` remove e-mails e números longos); parceiros veem nome/e-mail do cliente mascarados.
- `audit_log` é somente inclusão (gatilhos bloqueiam UPDATE/DELETE).
- CSP restritiva (`script-src 'self'`), `X-Content-Type-Options`, `Referrer-Policy`, cabeçalho `X-Environment`.
- Textos externos (descrições de fornecedor etc.) são dados: o agente de catálogo detecta frases imperativas e abre pendência `conteudo_suspeito`; nenhuma ferramenta de agente é controlada por texto.

## 4. Modelo de dados (principais tabelas)

| Domínio | Tabelas |
|---|---|
| Regiões | `regions` (moeda, idioma, fuso, vendas liberadas, nota fiscal, provedor de pagamento/logística, entidade vendedora, prazo de devolução, frete) |
| Parceiros | `partners` (modelo comercial, taxa), `partner_units` (país/estado/cidade, `location_status` confirmada/não confirmada + fonte), `partner_products` (autorização) |
| Usuários | `users` (papel, parceiro), `sessions` (hash do token) |
| Catálogo | `products`, `product_translations` (rascunho/revisado), `product_regions` (preço, preço B2B, custo, ponto de reposição) |
| Estoque | `warehouses`, `inventory` (CHECK `reserved ≤ on_hand`), `inventory_movements`, `reservations` |
| Vendas | `storefronts`, `storefront_products`, `orders` (vendedor, modelo, chave de idempotência, correlação), `order_items`, `payments`, `shipments`, `refunds`, `returns`, `support_tickets` |
| Comissões | `commission_entries` (apuração/estorno/ajuste; índice único impede apuração dupla) |
| Integração | `webhook_inbox` (status, tentativas, próxima tentativa, erro), `integrations` (módulo interno / integração conectada / link externo / planejada), `idempotency_keys` |
| Operação | `tasks` (+ `task_dependencies`, `task_comments`, `task_history`), `attachments` |
| Fechamentos | `closings` (tipo, período, fuso, revisão, status, liquidação, totais e divergências congelados), `payouts` |
| Agentes | `agents`, `agent_runs` (status, resumo, evidências, custo, tokens), `agent_tool_calls`, `policies`, `action_requests`, `system_flags` |
| Rastreio | `events` (origem, entidade, parceiro, correlação, status, **ocorrido_em ≠ recebido_em**), `audit_log` |

Valores monetários em inteiros (centavos). Datas em UTC ISO-8601; fechamentos guardam o fuso do período.

## 5. Etapas e critérios de conclusão

| Etapa | Critério de conclusão | Situação |
|---|---|---|
| 1. Arquitetura, banco, autenticação, permissões, fluxo completo em demonstração | Percurso parceiro cria página → pedido de teste → reserva → pagamento de teste → envio → comissão → centro reflete; cenários de recusa, duplicidade, falta de estoque, desconexão e cancelamento testados | **Concluída** (33 testes automatizados + teste de navegador) |
| 2. Centro 3D conectado ao fluxo | Prédios, armazéns, setores, avatares e pulsos guiados por dados/eventos reais; painéis por clique; busca, filtros, tabelas, modo leve, reduzir animações | **Versão inicial entregue**; pendente: revisão de usabilidade com usuários, acessibilidade por teclado no canvas, Three.js/GSAP oficiais |
| 3. Primeira região com integrações de teste | Stripe (ou PSP brasileiro) em sandbox com webhooks reais; transportadora em sandbox; regras fiscais definidas; PostgreSQL; ambiente `sandbox` publicado | Pendente — depende das decisões e credenciais da seção 6 |
| 4. Piloto real limitado | Integrações validadas em sandbox, contratos, política de reembolso, 2FA, backups testados, monitoramento | Pendente |
| 5. Expansão | Agentes Parceiros/Atendimento ativados, modelo B2B operacional, novas regiões | Pendente |

## 6. Integrações

| Integração | Situação | O que falta |
|---|---|---|
| Pagamentos — provedor de demonstração | **Funcional, simulado** (apenas `APP_ENV=demo`), webhooks HMAC reais contra o próprio servidor; cartões de teste 4242… (aprova) e 4000…0002 (recusa) | — |
| Logística — transportadora de demonstração | **Funcional, simulada** (código `DEMO-…`) | — |
| Stripe (sandbox) | Interface preparada (`StripeProvider`), **não implementada nem verificada** | Chaves de teste no servidor; decisão de PSP para o Brasil (cartões Elo/Hipercard, boleto e Pix exigem avaliação) |
| Transportadoras BR/PT/EUA | Planejada | Escolha de parceiros logísticos e credenciais |
| IA (API Anthropic) | Adaptador `llm_complete` escrito, **não verificado** (sem credencial/rede). Sem chave, agentes que dependem de IA ficam em **aguardando integração** | `ANTHROPIC_API_KEY` no servidor e teste em sandbox |
| WhatsApp Business, atendimento | Planejadas | Conta Meta, política de mensagens |
| Emissão fiscal (NF-e, faturação certificada PT) | Não iniciada | Decisão contábil por país |
| GSAP / Three.js | Opcionais (ver D2/D3) | Copiar arquivos em `web/vendor/` / build npm |

## 7. Agentes operacionais

Todos os cálculos (estoque, comissão, saldo, fechamentos) são código determinístico; agentes executam rotinas, investigam e preparam ações sob política. Cada execução registra status, resumo, ferramentas usadas (com entradas/saídas resumidas e sem dados pessoais), evidências e custo.

| Agente | Situação | Ferramentas | Limites | Gatilhos / frequência | Orçamento | Encaminha a humano quando |
|---|---|---|---|---|---|---|
| Coordenador | Ativo (determinístico) | listar tarefas, comentar, criar pendências | Não altera pedidos/estoque/pagamentos | 5 min, tarefa vencida | US$ 20/mês | tarefa crítica vencida, agente falhando, pendência sem responsável |
| Estoque e logística | Ativo (determinístico) | consultar estoque/pedidos, solicitar `estoque.compra` | Compra sempre via política | 10 min, tarefa atribuída | US$ 20/mês | ruptura, atraso > 48 h, compra acima do limite |
| Financeiro | Ativo (determinístico) | conciliar, preparar fechamentos | Prepara; não confere/aprova/liquida | 30 min | US$ 30/mês | qualquer divergência |
| Catálogo | Ativo; geração de texto **aguardando integração de IA** | consultar catálogo, rascunho de tradução | Só rascunhos; publicação humana | 60 min | US$ 50/mês | texto suspeito, atributo regulatório ausente |
| Análise | Ativo (determinístico, somente leitura) | métricas | — | 60 min | US$ 20/mês | margem negativa |
| CEO de IA (um por parceiro) | Ativo | pesquisa grátis, dados internos, previsão, cérebro, criar tarefas, contratar agentes internos | Só tarefas internas; gasto via política | ideia nova, diário | teto global do cérebro | perguntas ao dono, riscos, qualquer custo |
| Pesquisa | Ativo | notícias RSS grátis, IA local | Somente leitura | tarefa do CEO | grátis | fonte indisponível |
| Conteúdo | Ativo (precisa de IA local) | IA local | Só rascunhos, nunca publica | tarefa do CEO | grátis | todo rascunho é revisado |
| Parceiros | Desativado (Etapa 5) | — | — | sob demanda | US$ 30/mês | alteração comercial |
| Atendimento | Desativado; política `atendimento.responder` = proibido | — | — | 15 min | US$ 50/mês | reclamação, reembolso, ameaça legal |

Controles: pausa global (admin), status por avatar (disponível, executando, aguardando integração, aguardando decisão, falhou, concluído), orçamento mensal, segregação (agente não aprova o que solicitou), cancelamento informa efeitos externos já realizados e compensações possíveis.

## 7.1 CEO de IA de cada parceiro

Cada parceiro tem um **CEO de IA** (agente `ceo_<parceiro>`, avatar dourado no topo do prédio; o halo mostra o status real). Ele:

1. **Recebe uma ideia** (andar *Diretoria · CEO de IA* do prédio, ou `POST /api/partners/{id}/ideas`).
2. **Pesquisa**: notícias públicas gratuitas (Google Notícias RSS, cache de 12 h), dados internos do parceiro e **previsão de vendas por regressão linear** (código, não IA).
3. **Planeja** com o cérebro de nível *decisão*; a resposta é validada (formato, até 8 passos, só agentes permitidos, dependências remapeadas). Sem cérebro ou com resposta inválida → **plano-modelo** determinístico.
4. **Contrata** funcionários gratuitos (agentes internos — custo zero) e **distribui tarefas** com prazos e dependências.
5. Os agentes executam ao vivo: *Pesquisa* (manchetes + links), *Análise*/*Catálogo*/*Financeiro* (entregas determinísticas), *Estoque*, *Conteúdo* (rascunhos para revisão humana; nunca publica).
6. **Briefing diário** (uma vez por dia local): sinais do mercado, previsão e até 2 ajustes de tarefa.

Limites: o CEO só cria tarefas internas. Gastos, compras, campanhas, reembolsos e mudanças comerciais seguem as políticas da seção 7 (sem política → revisão humana). Textos externos ficam entre `<dados>` e nunca viram permissão. Parceiro vê só o próprio CEO.

### Cérebro misto (menor custo)

| Nível | Uso | Ordem | Custo |
|---|---|---|---|
| Rotina | briefing diário, resumos de pesquisa, rascunhos | Ollama local → (Claude só se `BRAIN_ROUTINE_FALLBACK_CLAUDE=1`) | grátis |
| Decisão | plano de uma ideia nova | **Ollama primeiro**; Claude (`BRAIN_DECISION_MODEL`, padrão Haiku 5.5) só se a IA grátis estiver fora do ar ou devolver plano inválido, dentro dos tetos `BRAIN_DAILY_CAP_USD` (US$ 0,20) e `BRAIN_MONTHLY_CAP_USD` (US$ 2) | grátis; ≈ US$ 0,0005 quando sobe para o Claude |
| Sem cérebro | — | regras determinísticas e plano-modelo | zero |

**Ativar a IA local grátis no Windows:** instale o Ollama (ollama.com), rode `ollama pull qwen2.5:3b` e reinicie o `iniciar.bat`.

**Claude (reserva paga):** copie `config.local.env.exemplo` para `config.local.env` e preencha `ANTHROPIC_API_KEY` (o arquivo fica só no seu PC, fora do Git). Travas: teto diário e mensal; ao atingir, segue só com IA grátis e regras; aviso automático (evento + pendência) ao chegar a 80% do mês. O consumo fica em `brain_usage` e aparece no prédio central (*Cérebro de IA*).

## 8. Como executar e acessar

**Windows:** dois cliques em `iniciar.bat` (instala o que faltar e sobe o servidor). `verificar.bat` roda os testes.

**Linux/macOS:**
```bash
pip install -r requirements.txt        # starlette, uvicorn, httpx, pytest, tzdata
APP_ENV=demo ./run.sh                  # http://127.0.0.1:8000
```

Compatibilidade Windows: fusos horários funcionam mesmo sem o pacote `tzdata` (`server/app/tz.py` tem as regras de São Paulo, Lisboa e Nova York, conferidas contra a base oficial em testes); tipos MIME de `.js/.css` são fixados no servidor (o registro do Windows às vezes os corrompe).

- Central: `admin@central.demo`, `ops@central.demo`, `financeiro@central.demo` · Parceiros: `marina@aurora.demo` (Brasil), `joao@lisboa.demo` (Portugal) · senha `demo1234` (somente demo).
- Loja: após o parceiro criar e publicar a página no andar **Comercial** do prédio, acesse `/loja/<slug>`.
- Variáveis: `APP_ENV` (demo|sandbox|production), `DATA_DIR`, `SCHEDULER_INTERVAL_S`, `STRIPE_SECRET_KEY`, `ANTHROPIC_API_KEY`.
- Reiniciar a demonstração: pare o servidor e apague `data/plataforma_demo.sqlite3*`.

## 9. Testes realizados

`python3 -m pytest` → **50 testes passando** (`test_fluxo`, `test_operacao`, `test_tz`, `test_ceo`), sem rede e sem gasto,, também com `FORCE_TZ_FALLBACK=1` (simula Windows sem tzdata):

- Percurso completo da Etapa 1 com verificação de eventos correlacionados, ocorrido/recebido, mascaramento de PII.
- Pagamento recusado (reserva liberada); cartão fora da lista de teste recusado.
- Pedido duplicado (mesma chave → mesmo pedido; conteúdo diferente → 409; sem chave → 400).
- Webhook duplicado (sem dupla baixa nem dupla comissão); eventos fora de ordem (autorizado após capturado e falha após confirmação ignorados).
- Falta de estoque atômica (nenhuma reserva parcial); assinatura inválida rejeitada.
- Cancelamento libera reserva; pagamento tardio após cancelamento gera reembolso sob política; cancelamento de pedido pago exige aprovação; cancelar ação executada não desfaz.
- Devolução estorna comissão; reserva expira pelo agendador.
- Retentativa limitada e fila de falhas com reprocessamento; recuperação de eventos após desconexão.
- Região bloqueada (EUA) e modalidade B2B não operacional; produto não autorizado.
- Isolamento entre parceiros (prédio, pedidos, tarefas, eventos, mundo); CSRF; sessão expirada; auditoria somente inclusão.
- Tarefas: dependências, evidências com SHA-256, histórico, recorrência executada sem navegador.
- Fechamentos: segregação de funções, justificativa para divergências, aprovado ≠ liquidado, reabertura preserva original, liquidado exige ajuste.
- Agentes: tarefa atribuída executada com evidência, compras preparadas sem política, pausa global, IA ausente → aguardando integração, texto de fornecedor tratado como dado, política proibida bloqueia agente.
- Sinalização: sem dados = cinza; falha de integração = amarelo; dados velhos = cinza.
- CEO: plano-modelo sem IA; plano por IA local validado (agente inexistente e instrução injetada descartados); Claude respeita o teto mensal; dependências respeitadas pelos agentes; Conteúdo sem IA fica *aguardando integração*; briefing uma vez por dia; isolamento entre parceiros; previsão determinística; cache de notícias.
- Fusos embutidos idênticos à base oficial a cada 3 h de 2025 a 2027, incluindo viradas de horário de verão.

Teste de navegador (`scripts/e2e.mjs`, Playwright/Chromium com WebGL por software): login, cidade 3D, prédio do parceiro brasileiro, criação de página pela interface, compra na loja com pagamento de teste, envio + comissão refletidos ao vivo, **queda e reinício do servidor** com pedido criado durante a desconexão e recebido após a reconexão, tabelas, visão móvel em modo leve, isolamento. Resultado: **E2E OK**.

## 9.1 Automação contínua (GitHub)

| Peça | O que faz | Custo |
|---|---|---|
| `.github/workflows/ci.yml` | A cada envio, PR e toda segunda-feira: testes em **Windows e Linux**, com fusos oficiais e embutidos, + teste de navegador com capturas. Falha na `main` → abre/atualiza issue **"CI falhando na main"** (o GitHub notifica); quando volta a passar, comenta e fecha | Gratuito em repositório público; privado usa a cota de minutos do GitHub |
| `.github/dependabot.yml` | Abre PRs semanais atualizando dependências; o CI testa cada um antes do merge | Gratuito |
| `.github/workflows/claude.yml` | **Opcional, sob demanda:** comentar `@claude …` numa issue/PR faz a IA propor a correção em PR, que passa pelo CI antes da sua aprovação. Só roda com o segredo `ANTHROPIC_API_KEY`; nunca roda sozinho | Tokens da API por uso (ver seção 12) — **não verificado ainda** |

## 10. O que está em simulação

Tudo no ambiente demo: parceiros, unidades, produtos, estoques, endereços, pagamentos (provedor de demonstração), envios, repasses (`DEMO-SEM-TRANSFERENCIA`), integrações "ERP do parceiro" e "Marketplace externo" (cenários para sinalização cinza/amarela). Indicações permanentes: faixa de ambiente, selos "DEMO"/"simulado" em pedidos, pagamentos, envios, fechamentos e repasses.

## 11. Limitações conhecidas

- SQLite com um processo; o tempo real lê a tabela de eventos a cada 1 s (latência ~1 s).
- 3D em WebGL próprio: sem sombras/modelos detalhados; acessibilidade por teclado no canvas limitada (o conteúdo completo está nas tabelas e no painel lateral).
- Avatares mudam de estado só quando há execução; rotinas determinísticas são rápidas, então "executando" raramente aparece por mais de um instante (comportamento real, não encenado).
- Atendimento ao cliente, metas comerciais e emissão fiscal não implementados.
- Impostos não calculados (D11). Comissão segue D6 até decisão comercial.
- Anexos gravados em disco local (`data/anexos_<env>`); produção requer armazenamento de objetos com antivírus.
- Sem 2FA, sem recuperação de senha, sem gestão de usuários pela interface.

## 12. Custos estimados

### Premissas
- Piloto (Etapa 4): 1 região, até 20 parceiros, até 2.000 pedidos/mês, 10 usuários internos.
- Preços de referência consultados em 09/11/2026 — revisar antes de contratar.

### Infraestrutura (US$/mês)

| Item | Demo/Etapa 1–2 | Piloto |
|---|---|---|
| Servidor de aplicação (VM 1 vCPU/1–2 GB → 2 vCPU/4 GB) | 6–12 | 24 |
| PostgreSQL gerenciado (1 GB básico → 2 GB) | 0 (SQLite) | 15–30 |
| Backups (≈20% da VM) | 1–2 | 5 |
| Armazenamento de anexos (objetos) | 0 | 5 |
| **Total aproximado** | **7–14** | **≈50–65** |

Referências: VM básica de 2 GB por cerca de US$ 12/mês e PostgreSQL gerenciado a partir de US$ 15/mês na DigitalOcean (páginas de preço públicas, 2026). Alta disponibilidade do banco aproximadamente dobra o item.

### Execução de IA (US$/mês)

Preços oficiais por milhão de tokens (entrada/saída): Claude Haiku 5.5 US$ 0,10/0,50; Sonnet 5.5 US$ 2/10; Opus 5.5 US$ 4/20 (platform.claude.com, consultado em 09/11/2026).

| Uso | Premissa | Custo estimado |
|---|---|---|
| Rotinas determinísticas (coordenação, estoque, financeiro, análise) | Sem chamadas de IA | 0 |
| Rascunhos de tradução (Haiku) | 1.500 tokens entrada + 600 saída por item; 1.000 itens/mês | ≈ 0,45 |
| Planos do CEO (Haiku, decisão) | 3.000 + 1.400 tokens; 100 ideias/mês | ≈ 0,10 |
| Briefings e rascunhos (Ollama local) | qualquer volume | 0 (usa o seu PC) |
| Investigação de divergências (Sonnet) | 4.000 + 800 tokens; 300/mês | ≈ 5 |
| Rascunhos de atendimento (Haiku → Sonnet) | 3.000 + 500 tokens; 2.000 chamados/mês | ≈ 1 (Haiku) a ≈ 22 (Sonnet) |
| **Teto configurado** (soma dos orçamentos dos agentes) | limite rígido | 220 |

Taxas de pagamento não estão incluídas: dependem do PSP escolhido (decisão pendente). A taxa de 3,49% usada na demonstração é fictícia e serve apenas para exercitar a conciliação.

## 13. Decisões e bloqueios pendentes

1. **PSP por país** (Brasil: cartões locais, Pix, boleto; Portugal: MB WAY/Multibanco; EUA) e credenciais de sandbox.
2. **Regras fiscais e entidade vendedora** por país (habilita vendas nos EUA; NF-e no Brasil; faturação certificada em Portugal).
3. **Regra de comissão** (base, momento de apuração, carência, periodicidade de repasse).
4. **Política de reembolso** (limites automáticos por moeda) e **compras de estoque** (hoje sem política → revisão).
5. Transportadoras por região; hospedagem e domínio do ambiente `sandbox`.
6. Chave da API de IA e orçamento mensal aprovado.

## 14. Próxima etapa (3)

Migrar para PostgreSQL; publicar ambiente `sandbox`; implementar e validar o adaptador do PSP escolhido com webhooks reais de teste; transportadora em sandbox para o Brasil; configurar regras fiscais definidas; ativar IA para o agente de catálogo com orçamento. Custo operacional estimado da etapa: ≈ US$ 50–65/mês de infraestrutura + até US$ 10/mês de IA.
