# VIRTUSX — documentação única

> Fonte única de verdade do projeto. Atualize este arquivo a cada etapa: decisões, arquitetura, modelo de dados, etapas, integrações, testes, limitações e custos.
> Última atualização: 10/11/2026 · Etapa 1 concluída · Etapa 2 em versão inicial · CEO de IA · **operação real de serviços de TI (produção)** · Windows · CI.

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

## 7.2 Operação real: serviços de TI (ambiente de produção)

Ambiente separado (`iniciar-producao.bat`, porta **8001**, faixa vermelha **PRODUÇÃO**, banco `data/plataforma_production.sqlite3`). Nasce limpo: só regiões, políticas e agentes; **nenhum dado fictício**. Demonstração (porta 8000) e produção podem ficar abertas juntas (cookies de sessão separados).

| Item | Como funciona |
|---|---|
| Primeiro acesso | Tela cria o administrador (uma única vez; senha ≥ 10). Depois, acessos só por *Cadastros*. |
| Bases / filiais | Suas unidades de prestação de serviço; aparecem no 3D como prédios técnicos (antena verde) ligados às unidades que atendem. |
| Clientes | `commercial_model='cliente_servico'`: segmento, contato, **contrato mensal**. Cada cliente ganha prédio, CEO de IA e módulos internos. |
| Unidades dos clientes | Cidade/UF, endereço (confirmado ou não) e **base que atende**. |
| Equipe e acessos | Administrador, operações, financeiro, acesso do cliente (gestor/operador) e **técnico/freelancer** (vinculado a uma base). Freelancer entra **aguardando autorização**; só vê e altera as próprias tarefas (status, checklist, comentários, evidências). |
| Checklists de TI | Modelos: preventiva, chamado, backup, inventário, implantação. Conclusão exige todos os itens; quem marcou e quando fica registrado. Preventiva pode ser recorrente (mensal) por unidade. |
| Fechamento mensal de serviços | Serviços concluídos por tipo/unidade, atrasados, evidências e valor do contrato; divergências (sem evidência, atrasos, sem contrato). Recebimento é registrado à parte com referência (NF/boleto). |
| CEO de serviços | Usa só Pesquisa, Análise, Conteúdo e Financeiro; foco em melhorar o serviço e ampliar o contrato. |

Próximo passo do modelo de negócio: **parceiros com mensalidade** (cobrança recorrente) — depende da escolha do meio de cobrança.

## 7.3 Mapa-múndi real, central de comandos e atendimento por WhatsApp

**Mapa real.** Contornos de todos os países (Natural Earth 1:50m, via `world-atlas`) e, ao abrir um país, de todos os seus estados/províncias (Natural Earth 1:10m admin-1). Baixados **uma vez** pelo servidor e guardados em `data/geo/` (depois funciona sem internet). Sem download, o mapa mostra o aviso e o restante segue funcionando. Clique num país → painel com estados e operações; clique num estado → unidades, bases e técnicos ali. Unidades são posicionadas pela cidade (marcadas **aproximadas**) ou pelas coordenadas que você digitar no cadastro (**confirmadas**). Cidades fora da lista interna são geolocalizadas pelo OpenStreetMap/Nominatim (1 consulta/s, com cache). Nada é apresentado como rastreamento ao vivo.

**Central de comandos** (`Ctrl+K` ou botão *Comandos*). Exemplos:
- `chamado Casa Aurora unidade Loja Pinheiros prazo amanhã: impressora sem rede`
- `preventiva Clínica Vida prazo 15/12`, `tarefa revisar contrato prazo sexta`
- `ideia <cliente>: <ideia para o CEO de IA>`, `rodar agente pesquisa`, `rodar ceo <cliente>`, `fechamento <cliente> 11/2026`, `whatsapp <cliente>: <mensagem>`, `pausar agentes` / `retomar agentes`, `abrir Brasil`
O texto é interpretado por regras fixas (sem custo); se não reconhecer, a IA local tenta reescrever no formato conhecido. Sempre mostra uma **prévia** antes de executar; só ações da lista permitida, sempre com as permissões de quem digitou.

**Quadro de tarefas** (*Tabelas → Quadro de tarefas*): colunas a fazer / em andamento / bloqueada / concluída; arrastar muda o status (as regras de checklist e evidência continuam valendo).

**Atendimento** (botão *Atendimento*, contador de não lidas): conversas do WhatsApp e canais internos. Em cada conversa: vincular ao cliente/unidade, responder, **sugerir resposta** (IA grátis local; vira rascunho, nunca é enviada sozinha), **abrir chamado** (tarefa com checklist de chamado) e encerrar. Mensagens de clientes são tratadas como dados, nunca como instruções.

### WhatsApp: o que você precisa fazer (uma vez)
No demo, há um botão para **simular** mensagens recebidas; respostas aparecem como "simulado". Em produção, sem credenciais, o envio fica **integração pendente** (nada é fingido). Para ligar o real:
1. Em developers.facebook.com crie um app tipo *Business* e adicione o produto **WhatsApp**; vincule sua conta WhatsApp Business (WABA) e um número.
2. Copie o **Phone number ID** e gere um **token permanente** (usuário do sistema no Business Manager com permissão `whatsapp_business_messaging`). Em *Configurações do app → Básico* copie a **Chave secreta do app**.
3. Preencha `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET` e invente um `WHATSAPP_VERIFY_TOKEN` no `config.local.env`; reinicie.
4. A Meta exige endereço **HTTPS público**. Grátis: `cloudflared tunnel --url http://127.0.0.1:8001` gera um endereço `https://….trycloudflare.com` (para endereço fixo, use um túnel nomeado com domínio próprio).
5. No painel do WhatsApp → *Configuração → Webhook*: URL `https://<seu-endereço>/api/webhooks/whatsapp`, token de verificação = o que você inventou; assine o campo **messages**.
6. Teste mandando uma mensagem para o número: ela aparece em *Atendimento*. Toda mensagem recebida tem a assinatura `X-Hub-Signature-256` conferida; sem assinatura válida é rejeitada.

Regras da Meta respeitadas: respostas livres só até **24 h** após a última mensagem do cliente; fora disso exige *modelo aprovado* (ainda **não implementado** — o sistema bloqueia e avisa). A Meta cobra por conversas iniciadas pela empresa; respostas dentro da janela de atendimento costumam ser gratuitas — confira a tabela vigente da Meta.

## 7.4 Vigia automático (sem falhas silenciosas)

- **Supervisor** (`server/rodar.py`, usado pelos `iniciar*.bat`): quando arquivos novos são salvos no disco, o servidor termina o que está fazendo e **reinicia sozinho com a versão nova**; se cair por erro, volta sozinho (espera 2 s → 60 s). Registro em `data/logs/supervisor.log`. Isso evita a falha "tela nova com servidor antigo" (ex.: *reading 'lat'* no 3D).
- **Tela sempre atualizada**: o navegador revalida os arquivos a cada abertura; se o servidor mudou de versão, aparece "Nova versão instalada" e a tela recarrega sozinha (sem perder o que está sendo digitado).
- **Erros da tela** (JavaScript) são enviados automaticamente ao servidor, agrupados e mostrados em **Saúde** (sem dados pessoais). Um item com dado ruim não derruba o mapa: é pulado e registrado.
- **Verificações a cada 5 min** (botão **Saúde** no cabeçalho): banco íntegro, agendador, versão em execução × disco, mapa e satélite, IA local, WhatsApp, webhooks na fila de falhas, agentes, erros de tela, cópia de segurança e espaço em disco. Vermelho gera evento `sistema.alerta` e **uma** pendência crítica para a central; ao normalizar, evento `sistema.normalizado`.
- **Cópia de segurança diária** consistente (com o sistema rodando) em `data/backups`, verificada (`quick_check`) e com 14 cópias guardadas. Para restaurar: feche o servidor e copie a cópia desejada sobre `data/plataforma_<ambiente>.sqlite3`.
- **Testes automáticos** (CI a cada envio ao GitHub): os 3 testes de navegador falham se o vigia registrar qualquer erro de tela; no CI o mapa real e a imagem de satélite precisam baixar de verdade.

**Mapa realista**: imagem de satélite NASA Blue Marble (domínio público, via NASA GIBS) no mundo inteiro; ao aproximar, imagem mais nítida só da área vista; opção **Ruas** (OpenStreetMap, com cache em disco e sem download em massa) e **Escuro** (vetorial). Unidades mostram colunas de operações: verde = feitas em 30 dias, âmbar/vermelho = abertas/atrasadas; tarefas criadas/concluídas geram pulso da base até a unidade.

## 7.5 Posições exatas e operações manuais para agentes

**Posição de cada empresa** (coluna *Localização* em Cadastros mostra a precisão real):
- **coordenadas informadas** — você digitou (prioridade máxima; o sistema nunca sobrescreve; editar o cadastro mantém);
- **endereço exato** — rua e número localizados no OpenStreetMap; só é aceito se cair a até 40 km do centro da cidade (evita "Rua X" de outra cidade);
- **centro da cidade** — enquanto o endereço não é localizado (ou se não houver endereço);
- **aguardando localização** — o agendador busca sozinho (até 3 endereços por volta, 1 consulta/s, cada endereço consultado uma única vez — inclusive quando não acha).
Dica: para precisão total, copie as coordenadas do Google Maps (clique direito no local → copiar "-22.37, -46.94") e cole no campo *Coordenadas*.

**Sede**: *Cadastros → Sede · administração central* (cidade, endereço ou coordenadas). Sem cadastro, o mapa usa a posição padrão e avisa no rótulo. Parceiros não veem o endereço da sede.

**Escala do mapa**: 1 unidade ≈ 0,55 km — prédios não cobrem cidades vizinhas; rótulos que se sobrepõem são escondidos por prioridade (país › sede › cliente › base › unidade › CEO › agentes) e aparecem ao aproximar.

**Operação manual para um agente**: abra o agente (mapa ou *Tabelas → Agentes*) → *Nova operação manual*: tipo (ex.: pesquisar mercado, escrever texto, analisar conta, revisar prazos), cliente, prazo, prioridade e instruções; "executar agora" dispara a rotina na hora. Vira uma tarefa atribuída ao agente (auditada); o resultado volta como comentário na tarefa. No CEO de um cliente, a operação entra como ideia para ele estudar e dividir com a equipe. As instruções não dão ao agente nenhuma permissão nova.

## 7.6 Navegação, CEO de cada base, crescimento dos clientes e casas dos freelancers

**Mouse (como Google Maps)**: arrastar = mover (o ponto sob o mouse acompanha o mouse) · roda = zoom em direção ao cursor · duplo clique = aproximar (Shift + duplo clique = afastar) · botão direito, Ctrl ou Shift + arrastar = girar e inclinar · dois dedos (celular) = zoom e giro. Teclado com o mapa em foco: setas, + / −, Q / E, PageUp/PageDown. Botões na tela: + − · N (norte) · 2D/3D · ⌂ (visão geral) · ? (ajuda). Qualquer toque do usuário interrompe voos automáticos da câmera. A legenda fica compacta (ⓘ mais para expandir).

**CEO de cada base** (um por base/filial, criado automaticamente, grátis/determinístico, a cada 30 min ou "Executar CEO agora" no painel da base): lê unidades atendidas, tarefas e carga dos técnicos; agenda a **preventiva mensal** de unidades sem nenhuma; **distribui** tarefas sem responsável pelos técnicos da base com menor carga — via política `tarefas.atribuir`: sem política, cria UMA solicitação por volta para você aprovar (Aprovações); com o botão "Distribuição: alternar automática" (admin) aplica direto; encaminha à central atrasos acima de 2 dias (uma pendência por tarefa). O resumo aparece no painel da base. Partners não veem bases, equipe nem resumos.

**Clientes crescem**: o prédio do cliente começa pequeno e sobe de nível conforme os serviços são concluídos (níveis em 3, 8, 20 e 50 serviços; coroa dourada a partir do nível 4). **Pontos de cashback**: cada serviço concluído (tarefa do cliente, não pendência) credita pontos — padrão 10, ajustável (`POST /api/cadastros/pontos`); crédito único por tarefa (nunca em dobro). Pontos são registro de fidelidade: **não movimentam dinheiro**; resgate é decisão da central. Painel do cliente → Recepção → *Fidelidade · cashback*.

**Bases** são campus grandes (torre, anexo, heliponto com o CEO da base, antena). **Freelancers** aparecem como casas ao redor da base (posição ilustrativa — não é localização real nem rastreamento), com tarefas abertas; aguardando autorização = casa translúcida. No demo há 2 freelancers fictícios (Rafa e Bia, base Demo em Guarulhos); em produção aparecem os seus freelancers reais cadastrados.

**Visual**: brilho do sol e luz de contorno nos prédios, sombras projetadas, céu azul-noite no horizonte.

## 7.7 Quem usa IA e a chave da Claude

- **CEO de cada cliente** (ideias, planos, briefing diário): usa o **cérebro misto** — primeiro a IA grátis local (Ollama, qwen2.5:3b); se ela falhar ou demorar, a **Claude** (chave `ANTHROPIC_API_KEY`, com teto diário/mensal); sem nenhuma das duas, plano determinístico. Tempo máximo da IA local: 4 min (rotina) / 7 min (decisão), pois no processador do PC um plano completo leva minutos.
- **Pesquisa (Helena) e Conteúdo (Igor)**: IA local; Claude só se `ROUTINE_FALLBACK_TO_CLAUDE` estiver ligado.
- **Ana (coordenação), CEO de cada base, Estoque, Financeiro, Catálogo, Análise**: **determinísticos e grátis** — não usam IA nem a chave (de propósito: contas e prazos não podem depender de IA).
- **Saúde → "Claude (chave da API)"**: testa a chave **sem gastar crédito** (consulta a lista de modelos), a cada hora ou em "Verificar agora"; mostra só o começo e o fim da chave e de onde ela foi lida. 401 = chave recusada (revogada, digitada errada ou de outra conta): gere outra em console.anthropic.com → API Keys.
- **Colar a chave pela tela** (só administrador): *Saúde → Chave da Claude → Salvar e testar*. A chave é testada sem custo e só é salva se a Anthropic aceitar; vai para `config.local.env` (só neste computador), vale na hora (sem reiniciar) e nunca mais aparece inteira. Identificador (`apikey_…`) e chave cortada são recusados com explicação. Campo vazio nunca apaga: para tirar a chave, use *Remover chave*.
- `config.local.env` tem prioridade sobre uma variável `ANTHROPIC_API_KEY` antiga do Windows; aspas e espaços em volta do valor são ignorados.
- Agente que recebe tarefa com **checklist** (trabalho físico) entrega o que pode como comentário e deixa a tarefa "aguardando terceiro" para uma pessoa — não fica falhando em loop.

## 7.8 Etapa 2 — Expansão internacional e Sentinela

**Expansão por país** (só administrador cria; operações vê): clique num país no mapa → *🌐 Expandir para este país* → marca, oferta, contato e preço local (idioma e moeda vêm do catálogo de ~60 países; fora dele, você informa). O sistema cria o **mercado** e o **CEO do país**, que pesquisa notícias no idioma local, escreve a estratégia (IA local → Claude com teto; sem IA, regras) e divide o trabalho:
- **Lia · Jurídico** — lei de dados, imposto sobre consumo, consumidor, registro, cookies (UE), Impressum (Alemanha). **Não é parecer**: sempre abre "Revisão por advogado local" para você.
- **Leo · Localização** — site do país no idioma e moeda locais (rascunho).
- **Mia · Marketing** — rascunhos de anúncios Google/Meta/marketplace com limites de caracteres; verba só via política `campanha.paga`.
- **Vitor · Vendas** — plataformas fortes no país (Mercado Livre, Amazon, Allegro, Bol.com, Rakuten, Shopee…) e passos; não cria contas.
- **Carla · Financeiro** — moeda, preço local (sem conversão inventada), imposto e meios de pagamento comuns.

**Presença remota, sem enganação**: o mapa mostra uma torre holográfica na capital ("remoto"); o site diz "atendimento remoto" e o JSON-LD não inventa endereço. Não criamos IP nem endereço falso. O Google encontra pelo idioma, `hreflang`, `sitemap.xml` e domínio — configure `PUBLIC_BASE_URL` com um endereço público. Presença física só quando houver endereço real.

**Publicação**: prévia em `/m/<slug>` (só equipe, `noindex`); publicar exige aprovação (`mercado.publicar_site`). Depois: `/m/<slug>`, `/sitemap.xml`, `/robots.txt`.

**Sentinela · monitor de segurança** (Saúde → Sentinela; diário ou "Rodar agora"): vulnerabilidades conhecidas nos pacotes (OSV.dev), versões novas (PyPI), segredos no arquivo de modelo, `.gitignore`, integridade do banco, cópia atrasada, força bruta no login, servidor exposto, erros de tela. Usa **chave separada** (`MONITOR_ANTHROPIC_API_KEY`, colável pela tela) com **teto de US$ 5/mês**, gasto separado dos CEOs; sem chave usa a IA local. **Só propõe**: achado alto/crítico vira uma solicitação `sistema.corrigir`; aprovada, abre issue no GitHub com `@claude` (a automação cria o PR, os testes rodam e você decide o merge) se `MONITOR_GITHUB_TOKEN`/`MONITOR_GITHUB_REPO` estiverem configurados; senão, a correção fica descrita.

**Login protegido**: após 10 senhas erradas em 5 min do mesmo endereço, bloqueio de 5 min; tentativas contam para o Sentinela.

## 7.9 Negócio profissional: identidade visual, conversão e captação

- **Identidade visual** (Cadastros → Identidade visual, administrador): nome da marca, slogan, tom, 3 cores (principal, secundária, destaque dos botões) e fonte, com prévia. Aplicada em todos os sites de país.
- **Site de alta conversão** (cada país): chamada clara, benefícios, "como funciona" em 3 passos, preço, perguntas frequentes (com dados estruturados para o Google), formulário "Peça seu orçamento", botão fixo de WhatsApp, Open Graph para compartilhar. Sem scripts (rápido e seguro), funciona no celular. Nada inventado: sem depoimentos, números ou selos falsos.
- **Captação**: formulário com **consentimento obrigatório** (LGPD/GDPR), anti-spam (campo isca + no máximo 5 envios por endereço a cada 10 min), origem do anúncio (`utm_source`, `utm_campaign`). Cada lead vira tarefa "responder em 24 h"; o evento não leva dados pessoais. Parceiros não veem leads.
- **Funil por país** (painel do país): visitas do site (contadas no servidor, sem cookies; a prévia não conta) → leads → ganhos, conversões e origens. **Tabelas → Leads**: todos os leads, com situação (novo, contatado, proposta, ganho, perdido).
- Dica: nos anúncios use links como `https://seu-dominio/m/<pais>?utm_source=google&utm_campaign=lancamento`.

## 7.10 Financeiro manual, estoque por operação, vitrine e parceiros

- **Financeiro manual** (Tabelas → Financeiro; perfis com `fin.write`): receitas e despesas de **qualquer operação** — sede, cada base, cada cliente, cada país — com categoria (ex.: mensalidade de parceiro, serviços, salários/freelancers, materiais, impostos, marketing), valor em centavos, moeda, data e situação **previsto** (a pagar/receber) ou **realizado**. Totais por operação/mês (receitas, despesas, resultado e previstos). Nada é apagado: erro = cancelar com motivo (fica na auditoria). Não movimenta dinheiro real.
- **Estoque das operações** (Tabelas → Estoque das operações; `estoque.write`): itens por operação (SKU, unidade, mínimo, custo), movimentos entrada/saída/ajuste com motivo obrigatório, saldo **nunca negativo**; abaixo do mínimo abre pendência "estoque baixo", que fecha sozinha quando o saldo volta.
- **Vitrine/catálogo do site** (painel do país → Vitrine e catálogo): itens com foto (JPG/PNG/WEBP até 2 MB, conferidos pelo conteúdo do arquivo, servidos só se o item estiver no site), descrição, preço em texto e ordem; ocultar/mostrar/remover. Cada item tem "Pedir orçamento deste item", que já preenche o pedido. **Layout por país**: Clássico, Vitrine (catálogo em destaque) ou Direto ao ponto.
- **Sobre nós e "Seja nosso parceiro"** (Cadastros → Identidade visual): texto institucional e até 3 planos de mensalidade (nome, valor, benefícios) exibidos nos sites com formulário próprio. O interessado vira lead **parceiro** com o plano escolhido e uma tarefa para a equipe. A **cobrança automática** da mensalidade depende de escolher o meio de pagamento (Pix/boleto/cartão); até lá, os recebimentos são lançados no Financeiro manual.

## 8. Como executar e acessar

**Windows:** `iniciar.bat` = demonstração (porta 8000); `iniciar-producao.bat` = operação real (porta 8001). Ambos instalam o que faltar e **ligam o Ollama** (baixam o modelo na primeira vez). `verificar.bat` roda os testes.

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

`python3 -m pytest` → **115 testes passando** (`test_fluxo`, `test_operacao`, `test_tz`, `test_ceo`, `test_producao`, `test_geo`, `test_canais_comandos`, `test_saude`, `test_localizacao_operacoes`, `test_ceo_base_pontos`, `test_agentes_chave`, `test_mercados_monitor`, `test_vitrine`, `test_operacional`), sem rede e sem gasto, também com `FORCE_TZ_FALLBACK=1` (simula Windows sem tzdata):

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
- Mapa: decodificação do TopoJSON, recorte de estados por país, geolocalização com cache e posição aproximada × confirmada.
- Comandos: interpretação, prévia, permissões e lista de ações permitidas. WhatsApp: verificação do webhook, assinatura inválida rejeitada, mensagem duplicada ignorada, janela de 24 h, produção sem credenciais = integração pendente, isolamento por parceiro.
- Fusos embutidos idênticos à base oficial a cada 3 h de 2025 a 2027, incluindo viradas de horário de verão.

Testes de navegador: `scripts/e2e_producao.mjs` (produção limpa: primeiro acesso, cadastros pela tela, freelancer autorizado, chamado com checklist concluído pelo celular com evidência, bases no 3D) e (`scripts/e2e.mjs`, Playwright/Chromium com WebGL por software): login, cidade 3D, prédio do parceiro brasileiro, criação de página pela interface, compra na loja com pagamento de teste, envio + comissão refletidos ao vivo, **queda e reinício do servidor** com pedido criado durante a desconexão e recebido após a reconexão, tabelas, visão móvel em modo leve, isolamento. Resultado: **E2E OK**. `scripts/e2e_mapa_canais.mjs`: mapa-múndi → Brasil → estados → São Paulo com unidades, comando criando chamado, arrastar no quadro, WhatsApp simulado → vincular cliente → responder → abrir chamado (no CI baixa o mapa real).

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
- WhatsApp: modelos de mensagem (fora da janela de 24 h) e mídia (fotos/áudios recebidos aparecem só como aviso) não implementados. Metas comerciais e emissão fiscal não implementados.
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
