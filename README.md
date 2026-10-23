# Sucesso do Cliente

Painel de saúde da base e de atendimento, alimentado por WhatsApp, Slack, Sponte e pelos registros de consultoria.

## O que o sistema faz

- **Recebe as conversas** do WhatsApp e do Slack em tempo real (webhooks) e liga cada mensagem ao cliente pelo telefone, grupo ou canal.
- **Puxa o financeiro do Sponte**: parcelas pagas, a vencer e vencidas.
- **Registra consultorias**: presença, tarefas combinadas e cumpridas, nota do cliente e anotações.
- **Analisa o sentimento** de cada mensagem do cliente com o Claude: tom de -1 a +1, categoria (dúvida, reclamação, financeiro…) e sinal de risco de cancelamento.
- **Calcula o health score** (0–100) de cada cliente e grava uma foto por dia, para acompanhar a evolução.
- **Mede o atendimento**: volume por canal, tempo mediano de resposta, % respondidas em até 1h e conversas paradas há mais de 24h.

## Telas

- **Hoje**: a lista de prioridades gerada a partir dos dados (fazer hoje, esta semana, quando der), o resumo da carteira e o semáforo das 10 leis.
- **Carteira**: todos os clientes do mais crítico ao mais saudável, com filtros por faixa, responsável e programa.
- **Atendimento**: volume por canal, tempo de resposta e conversas paradas.
- **10 Leis do CS**: cada lei com indicador, meta, o que fazer e os clientes que precisam de atenção.
- **Cliente**: saúde e pilares, próximos passos, jornada (fit, tempo de casa, primeiro resultado, último contato, renovação), resultados conquistados, consultorias, financeiro e conversas.

## As 10 leis do Customer Success (Nick Mehta)

| Lei | Indicador | Meta padrão |
|---|---|---|
| 1. Venda para o cliente certo | % da carteira com fit alto ou médio; score por fit; fit baixo por vendedor | 90%+ |
| 2. O distanciamento é natural | % de clientes com contato proativo da equipe nos últimos 14 dias | 90%+ |
| 3. O cliente espera resultados extraordinários | % com resultado comprovado (meta ou ganho) em 90 dias; valor gerado | 70%+ |
| 4. Monitore a saúde do cliente | % dos clientes em atenção/risco com ação da equipe em 7 dias | 90%+ |
| 5. Relacionamento pessoal não garante fidelidade | Clientes com sentimento bom, mas sem resultado comprovado | nenhum |
| 6. O produto é o diferencial escalável | Diferença de score médio entre responsáveis de CS; score por programa | até 10 pontos |
| 7. Reduza o tempo até o primeiro resultado | Mediana de dias entre o início do contrato e o primeiro resultado | até 30 dias |
| 8. Conheça profundamente seus indicadores | Churn de receita em 90 dias; MRR, churn de clientes, NRR 12 meses, motivos | até 5% |
| 9. Gerencie CS com métricas concretas | NPS das notas dadas nas consultorias; tempo de resposta | 50+ |
| 10. CS é responsabilidade de toda a empresa | De qual área vêm os alertas dos clientes em risco (comercial, financeiro, entrega, atendimento, CS) | até 10% da carteira em risco |

Para alimentar as leis, preencha no cadastro do cliente o **fit**, **quem vendeu**, o **resultado esperado**, a **data de início** e o **valor inicial**; registre os **resultados conquistados** e a **nota** das consultorias na página do cliente; e, ao cancelar, informe **data e motivo**. As metas das leis 2 e 7 mudam no `.env` (`META_TOQUE_DIAS`, `META_PRIMEIRO_VALOR_DIAS`); as regras estão em `app/leis.py`. Os mesmos dados saem em JSON em `GET /api/leis`.

## Health score

| Pilar | Peso padrão | Como é medido |
|---|---|---|
| Engajamento | 30 | Dias desde a última mensagem do cliente (100 até 7 dias, 0 com 45+) e volume de mensagens no mês |
| Sentimento | 25 | Média do sentimento dos últimos 60 dias, com mais peso para o recente; −15 por mensagem com sinal de churn nos últimos 30 dias |
| Financeiro | 25 | Parcelas vencidas (até 5 dias = 80, até 15 = 50, até 30 = 25, mais de 30 = 0); −10 por pagamento atrasado nos últimos 6 meses |
| Resultado | 20 | Presença nas consultorias (90 dias), % de tarefas cumpridas, nota do cliente e progresso no programa |

Faixas: **Saudável** a partir de 70, **Atenção** de 40 a 69, **Risco** abaixo de 40. Pilar sem dados fica de fora e o peso é redistribuído, então cliente novo não é punido por falta de histórico. Os pesos mudam no `.env`, e as regras ficam em `app/health.py`.

## Rodando localmente

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # preencha as chaves
python -m app.cli demo        # opcional: dados fictícios para conhecer o painel
uvicorn app.main:app --reload # abre em http://localhost:8000
```

O painel pede senha quando `PAINEL_TOKEN` está preenchido. O usuário pode ser qualquer um; a senha é o token.

## Rotina automática

Agende, por exemplo de hora em hora (cron, agendador do servidor):

```bash
python -m app.cli sync
```

Ele sincroniza o Sponte, puxa o que faltou do Slack, analisa o sentimento das mensagens novas e recalcula os scores. Também dá para rodar pelo botão **Sincronizar agora** no painel.

## Conectando cada fonte

O servidor precisa estar num endereço público com HTTPS (Railway, Render, Fly.io, uma VPS…) para receber webhooks. Há um `Dockerfile` pronto.

### WhatsApp

Aceita três provedores. A URL do webhook é sempre `https://SEU-DOMINIO/webhooks/whatsapp`.

- **Meta WhatsApp Cloud API (oficial).** No painel do app da Meta, em WhatsApp → Configuração, cadastre a URL e o `WHATSAPP_VERIFY_TOKEN`, e assine o campo `messages` (e `smb_message_echoes`, se usar o app WhatsApp Business junto, para registrar as respostas da equipe). Preencha `WHATSAPP_APP_SECRET` para validar a assinatura.
- **Evolution API.** Configure o webhook da instância com o evento `MESSAGES_UPSERT`. Se definir `WHATSAPP_WEBHOOK_SECRET`, mande o mesmo valor no header `apikey` ou como `?token=` na URL.
- **Z-API.** Em Webhooks, use a URL acima em "Ao receber" e "Ao enviar" (para contar as respostas da equipe). O segredo vai como `?token=` na URL.

Atendimento em **grupo por cliente**: copie o ID do grupo (termina em `@g.us`) no cadastro do cliente, ou vincule pela tela **Contatos sem cliente**.

### Slack

1. Crie um app em api.slack.com/apps com os escopos de bot `channels:history`, `groups:history` e `channels:read`.
2. Em **Event Subscriptions**, use a URL `https://SEU-DOMINIO/webhooks/slack` e assine `message.channels` e `message.groups`.
3. Preencha `SLACK_BOT_TOKEN` e `SLACK_SIGNING_SECRET`, convide o bot para os canais dos clientes e coloque o ID do canal (ex.: `C0123ABCD`) no cadastro.
4. Carga do histórico: `python -m app.cli slack 90`.

Em canais **Slack Connect**, mensagens de pessoas de outro workspace contam como cliente e as do seu workspace, como equipe. Em canais internos (sem o cliente dentro), tudo conta como equipe.

### Sponte

> ⚠️ Não encontrei documentação pública da API do Sponte. O conector segue o padrão do web service `WSAPIEdu.asmx` (parâmetros `nCodigoCliente`, `sToken`, `sParametrosBusca`), mas **os nomes dos métodos e campos precisam ser confirmados** com o suporte do Sponte antes de usar.

1. Peça ao Sponte o código de cliente, o token de integração e a documentação da API.
2. Preencha `SPONTE_CODIGO_CLIENTE`, `SPONTE_TOKEN`, e ajuste `SPONTE_METODO_ALUNOS` / `SPONTE_METODO_PARCELAS` conforme a documentação.
3. Se os campos do XML tiverem outros nomes, acrescente-os em `CAMPOS_ALUNO` / `CAMPOS_PARCELA` em `app/connectors/sponte.py`.
4. Rode `python -m app.cli sponte`. Com `--criar`, alunos que ainda não existem no sistema são cadastrados como clientes.

O vínculo é pelo campo **Código no Sponte** do cliente.

### Consultorias

Registre pela página do cliente. Ou automatize (Google Agenda, formulário, Zapier/Make) chamando a API:

```bash
curl -u x:SEU_PAINEL_TOKEN -X POST https://SEU-DOMINIO/api/consultorias \
  -H 'Content-Type: application/json' \
  -d '{"cliente_id": 1, "data": "2026-10-23T10:00", "compareceu": true,
       "tarefas_combinadas": 4, "tarefas_cumpridas": 3, "nota_cliente": 9,
       "notas": "Cliente animado, DRE implantado"}'
```

As notas entram na linha do tempo e na análise de sentimento.

Resultados do cliente (leis 3, 5 e 7) também podem vir por API:

```bash
curl -u x:SEU_PAINEL_TOKEN -X POST https://SEU-DOMINIO/api/marcos \
  -H 'Content-Type: application/json' \
  -d '{"cliente_id": 1, "data": "2026-10-23", "tipo": "ganho",
       "descricao": "Reduziu 8% dos custos fixos", "valor_gerado": 12000}'
```

`tipo` pode ser `primeiro_valor`, `meta` ou `ganho`.

### Outras fontes

`POST /api/interacoes` recebe qualquer interação (e-mail, formulário, ligação):
`{"canal": "email", "direcao": "entrada", "texto": "...", "cliente_id": 1}`.

## Análise de sentimento (Claude)

Preencha `ANTHROPIC_API_KEY`. As mensagens de cada cliente vão em lotes de até 40, com o contexto da conversa, para o modelo `claude-opus-5-5`, com esforço `low` (configurável em `CLAUDE_MODEL` / `CLAUDE_EFFORT`). O fallback automático do servidor está ativado (`fallbacks: "default"`): se o modelo recusar uma análise por engano, a própria API repete a chamada em outro modelo. Só as mensagens do cliente são analisadas, não as da equipe.

## Estrutura

```
app/
  main.py          rotas: painel, webhooks e API
  health.py        cálculo do health score e alertas por área
  leis.py          as 10 leis do Customer Success
  painel.py        carteira calculada e lista de prioridades
  atendimento.py   indicadores de atendimento
  sentiment.py     análise de sentimento com o Claude
  sync.py          rotinas de sincronização
  ingest.py        grava interações e liga ao cliente
  connectors/      whatsapp.py, slack.py, sponte.py
  templates/       telas do painel
tests/             pytest
```

Testes: `pytest`.

Ao atualizar o sistema, as colunas novas são criadas sozinhas no banco existente na próxima inicialização.

Em produção, troque o SQLite por Postgres em `DATABASE_URL`. O sistema guarda conversas de clientes, então trate o banco e o `.env` como dados sensíveis (LGPD).
