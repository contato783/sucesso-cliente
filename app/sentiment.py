"""Análise de sentimento das mensagens dos clientes com o Claude.

Agrupa as mensagens pendentes de cada cliente e manda em lote, para o
modelo ler o contexto da conversa e gastar menos chamadas.
"""

import json
import logging

import anthropic
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Interacao

log = logging.getLogger(__name__)

LOTE = 40

INSTRUCOES = """Você analisa conversas entre uma consultoria de gestão para pequenas e médias \
empresas e seus clientes (WhatsApp, Slack e notas de consultoria), para medir a saúde do relacionamento.

Para cada mensagem do cliente, devolva:
- sentimento: de -1 (muito negativo) a 1 (muito positivo); 0 é neutro/operacional.
- categoria: duvida, elogio, reclamacao, financeiro, agendamento, resultado, desengajamento ou outro.
- sinal_risco: true só se a mensagem indicar risco real de cancelamento ou não renovação \
(quer cancelar, fala em parar, reclama de valor, diz que não vê resultado, some de compromissos, \
compara com concorrente). Dúvidas e atrasos pontuais não são risco.

Considere o contexto das mensagens anteriores. Escreva em português."""

SCHEMA = {
    "type": "object",
    "properties": {
        "mensagens": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "sentimento": {"type": "number"},
                    "categoria": {
                        "type": "string",
                        "enum": ["duvida", "elogio", "reclamacao", "financeiro", "agendamento",
                                 "resultado", "desengajamento", "outro"],
                    },
                    "sinal_risco": {"type": "boolean"},
                },
                "required": ["id", "sentimento", "categoria", "sinal_risco"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["mensagens"],
    "additionalProperties": False,
}


def _classificar(client: anthropic.Anthropic, lote: list[Interacao]) -> dict[int, dict]:
    s = get_settings()
    linhas = [
        f"[id={i.id}] {i.ocorrido_em:%d/%m %H:%M} ({i.canal}) {i.autor or 'cliente'}: {i.texto}"
        for i in lote
    ]
    response = client.beta.messages.create(
        model=s.claude_model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=INSTRUCOES,
        output_config={"effort": s.claude_effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": "Mensagens do cliente:\n\n" + "\n".join(linhas)}],
    )
    if response.stop_reason == "refusal":
        log.warning("Análise recusada (request %s)", response._request_id)
        return {}
    if response.stop_reason == "max_tokens":
        log.warning("Resposta cortada por max_tokens (request %s)", response._request_id)
        return {}
    texto = next(b.text for b in response.content if b.type == "text")
    return {m["id"]: m for m in json.loads(texto)["mensagens"]}


def analisar_pendentes(db: Session, limite: int = 500) -> int:
    """Classifica mensagens de clientes ainda não analisadas. Retorna quantas foram processadas."""
    pendentes = db.scalars(
        select(Interacao)
        .where(Interacao.analisado.is_(False), Interacao.cliente_id.is_not(None))
        .order_by(Interacao.cliente_id, Interacao.ocorrido_em)
        .limit(limite)
    ).all()
    if not pendentes:
        return 0

    client = anthropic.Anthropic()
    por_cliente: dict[int, list[Interacao]] = {}
    for i in pendentes:
        por_cliente.setdefault(i.cliente_id, []).append(i)

    feitas = 0
    for interacoes in por_cliente.values():
        for inicio in range(0, len(interacoes), LOTE):
            lote = interacoes[inicio:inicio + LOTE]
            try:
                resultado = _classificar(client, lote)
            except anthropic.RateLimitError:
                log.warning("Limite de requisições do Claude atingido; o restante fica para a próxima rodada")
                db.commit()
                return feitas
            except anthropic.APIStatusError as e:
                log.error("Erro do Claude (%s): %s", e.status_code, e.message)
                continue
            except anthropic.APIConnectionError:
                log.error("Sem conexão com a API do Claude")
                db.commit()
                return feitas
            for i in lote:
                r = resultado.get(i.id)
                if r is None:
                    continue
                i.sentimento = max(-1.0, min(1.0, float(r["sentimento"])))
                i.categoria = r["categoria"]
                i.sinal_risco = bool(r["sinal_risco"])
                i.analisado = True
                feitas += 1
            db.commit()
    return feitas
