"""Indicadores de atendimento: volume, tempo de resposta e conversas sem resposta."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

from app.db import agora_utc
from app.models import Interacao


@dataclass
class Atendimento:
    volume_por_canal: dict[str, int] = field(default_factory=dict)
    mensagens_clientes: int = 0
    mensagens_equipe: int = 0
    tempo_resposta_mediano_min: float | None = None
    respondidas_em_1h: float | None = None  # fração
    sem_resposta: list[Interacao] = field(default_factory=list)  # última msg do cliente, sem retorno


def calcular(interacoes: list[Interacao], agora: datetime | None = None, dias: int = 30,
             sem_resposta_horas: int = 24) -> Atendimento:
    agora = agora or agora_utc()
    inicio = agora - timedelta(days=dias)
    periodo = sorted((i for i in interacoes if i.ocorrido_em >= inicio and i.canal != "consultoria"),
                     key=lambda i: i.ocorrido_em)
    a = Atendimento(
        volume_por_canal=dict(Counter(i.canal for i in periodo)),
        mensagens_clientes=sum(i.direcao == "entrada" for i in periodo),
        mensagens_equipe=sum(i.direcao == "saida" for i in periodo),
    )

    # Uma conversa = mesmo canal + mesma origem (telefone, grupo ou canal do Slack)
    conversas: dict[tuple, list[Interacao]] = {}
    for i in periodo:
        conversas.setdefault((i.canal, i.origem), []).append(i)

    tempos: list[float] = []
    for msgs in conversas.values():
        aguardando: datetime | None = None
        for m in msgs:
            if m.direcao == "entrada" and aguardando is None:
                aguardando = m.ocorrido_em
            elif m.direcao == "saida" and aguardando is not None:
                tempos.append((m.ocorrido_em - aguardando).total_seconds() / 60)
                aguardando = None
        if aguardando is not None and agora - aguardando > timedelta(hours=sem_resposta_horas):
            a.sem_resposta.append(next(m for m in reversed(msgs) if m.direcao == "entrada"))

    if tempos:
        a.tempo_resposta_mediano_min = round(median(tempos), 1)
        a.respondidas_em_1h = sum(t <= 60 for t in tempos) / len(tempos)
    a.sem_resposta.sort(key=lambda m: m.ocorrido_em)
    return a
