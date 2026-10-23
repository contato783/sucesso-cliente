"""Cálculo do health score do cliente.

Quatro pilares, cada um de 0 a 100. Pilar sem dados fica de fora e os pesos
dos demais são redistribuídos, para um cliente novo não ser punido por falta
de histórico.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Cliente, Consultoria, HealthSnapshot, Interacao, Parcela

SAUDAVEL = 70
ATENCAO = 40


@dataclass
class Health:
    score: float
    faixa: str
    engajamento: float | None
    sentimento: float | None
    financeiro: float | None
    resultado: float | None
    alertas: list[str] = field(default_factory=list)


def faixa_do_score(score: float) -> str:
    if score >= SAUDAVEL:
        return "saudavel"
    if score >= ATENCAO:
        return "atencao"
    return "risco"


def _linear(valor: float, bom: float, ruim: float) -> float:
    """100 quando valor <= bom, 0 quando valor >= ruim, linear no meio."""
    if valor <= bom:
        return 100.0
    if valor >= ruim:
        return 0.0
    return 100.0 * (ruim - valor) / (ruim - bom)


def pilar_engajamento(interacoes: list[Interacao], hoje: date, alertas: list[str]) -> float | None:
    do_cliente = [i for i in interacoes if i.direcao == "entrada"]
    if not interacoes:
        return None
    ultima = max((i.ocorrido_em for i in do_cliente), default=None)
    if ultima is None:
        alertas.append("Cliente nunca respondeu: só há mensagens da equipe")
        return 0.0
    dias = (hoje - ultima.date()).days
    recencia = _linear(dias, 7, 45)
    if dias > 14:
        alertas.append(f"Sem mensagem do cliente há {dias} dias")
    limite = datetime.combine(hoje - timedelta(days=30), datetime.min.time())
    no_mes = sum(1 for i in do_cliente if i.ocorrido_em >= limite)
    frequencia = min(no_mes / 8, 1.0) * 100
    return round(0.6 * recencia + 0.4 * frequencia, 1)


def pilar_sentimento(interacoes: list[Interacao], hoje: date, alertas: list[str]) -> float | None:
    limite = datetime.combine(hoje - timedelta(days=60), datetime.min.time())
    analisadas = [
        i for i in interacoes
        if i.analisado and i.sentimento is not None and i.direcao == "entrada" and i.ocorrido_em >= limite
    ]
    if not analisadas:
        return None
    # Mensagens recentes pesam mais (meia-vida de ~15 dias)
    soma = peso_total = 0.0
    for i in analisadas:
        peso = 0.5 ** ((hoje - i.ocorrido_em.date()).days / 15)
        soma += i.sentimento * peso
        peso_total += peso
    media = soma / peso_total
    nota = (media + 1) * 50
    limite_risco = datetime.combine(hoje - timedelta(days=30), datetime.min.time())
    riscos = sum(1 for i in analisadas if i.sinal_risco and i.ocorrido_em >= limite_risco)
    if riscos:
        alertas.append(f"{riscos} mensagem(ns) com sinal de churn nos últimos 30 dias")
        nota -= 15 * riscos
    if media < -0.2:
        alertas.append("Sentimento recente negativo nas conversas")
    return round(max(0.0, min(100.0, nota)), 1)


def pilar_financeiro(parcelas: list[Parcela], hoje: date, alertas: list[str]) -> float | None:
    if not parcelas:
        return None
    vencidas = [p for p in parcelas if not p.pago and p.vencimento < hoje]
    if vencidas:
        atraso = max((hoje - p.vencimento).days for p in vencidas)
        total = sum(p.valor for p in vencidas)
        valor = f"{total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        alertas.append(f"{len(vencidas)} parcela(s) vencida(s), R$ {valor}, atraso máximo de {atraso} dias")
        if atraso <= 5:
            nota = 80.0
        elif atraso <= 15:
            nota = 50.0
        elif atraso <= 30:
            nota = 25.0
        else:
            nota = 0.0
    else:
        nota = 100.0
    seis_meses = hoje - timedelta(days=180)
    pagas_atrasadas = sum(
        1 for p in parcelas
        if p.pago and p.data_pagamento and p.vencimento >= seis_meses
        and (p.data_pagamento - p.vencimento).days > 3
    )
    nota -= 10 * pagas_atrasadas
    return round(max(0.0, nota), 1)


def pilar_resultado(
    cliente: Cliente, consultorias: list[Consultoria], hoje: date, alertas: list[str]
) -> float | None:
    componentes: list[tuple[float, float]] = []  # (nota, peso)
    limite = datetime.combine(hoje - timedelta(days=90), datetime.min.time())
    recentes = sorted(
        (c for c in consultorias if limite <= c.data <= datetime.combine(hoje, datetime.max.time())),
        key=lambda c: c.data,
    )
    if recentes:
        presenca = sum(c.compareceu for c in recentes) / len(recentes)
        componentes.append((presenca * 100, 0.4))
        if len(recentes) >= 2 and not any(c.compareceu for c in recentes[-2:]):
            alertas.append("Faltou às duas últimas consultorias")
        combinadas = sum(c.tarefas_combinadas or 0 for c in recentes)
        if combinadas:
            cumpridas = sum(c.tarefas_cumpridas or 0 for c in recentes)
            taxa = min(cumpridas / combinadas, 1.0)
            componentes.append((taxa * 100, 0.3))
            if taxa < 0.4:
                alertas.append(f"Cumpriu só {taxa:.0%} das tarefas combinadas")
        notas = [c.nota_cliente for c in recentes if c.nota_cliente is not None]
        if notas:
            componentes.append((sum(notas) / len(notas) * 10, 0.2))
    if cliente.progresso is not None:
        componentes.append((float(cliente.progresso), 0.3))
    if not componentes:
        return None
    peso_total = sum(p for _, p in componentes)
    return round(sum(n * p for n, p in componentes) / peso_total, 1)


def calcular(
    cliente: Cliente,
    interacoes: list[Interacao],
    consultorias: list[Consultoria],
    parcelas: list[Parcela],
    hoje: date | None = None,
) -> Health:
    hoje = hoje or date.today()
    s = get_settings()
    alertas: list[str] = []
    pilares = {
        "engajamento": (pilar_engajamento(interacoes, hoje, alertas), s.peso_engajamento),
        "sentimento": (pilar_sentimento(interacoes, hoje, alertas), s.peso_sentimento),
        "financeiro": (pilar_financeiro(parcelas, hoje, alertas), s.peso_financeiro),
        "resultado": (pilar_resultado(cliente, consultorias, hoje, alertas), s.peso_resultado),
    }
    com_dados = [(nota, peso) for nota, peso in pilares.values() if nota is not None]
    if com_dados:
        score = sum(n * p for n, p in com_dados) / sum(p for _, p in com_dados)
    else:
        score = 50.0
        alertas.append("Sem dados suficientes: conecte as fontes ou registre uma consultoria")

    if cliente.data_renovacao:
        dias = (cliente.data_renovacao - hoje).days
        if 0 <= dias <= 45:
            alertas.append(f"Renovação em {dias} dias")

    score = round(score, 1)
    return Health(
        score=score,
        faixa=faixa_do_score(score),
        engajamento=pilares["engajamento"][0],
        sentimento=pilares["sentimento"][0],
        financeiro=pilares["financeiro"][0],
        resultado=pilares["resultado"][0],
        alertas=alertas,
    )


def calcular_cliente(db: Session, cliente: Cliente, hoje: date | None = None) -> Health:
    interacoes = list(db.scalars(select(Interacao).where(Interacao.cliente_id == cliente.id)))
    consultorias = list(db.scalars(select(Consultoria).where(Consultoria.cliente_id == cliente.id)))
    parcelas = list(db.scalars(select(Parcela).where(Parcela.cliente_id == cliente.id)))
    return calcular(cliente, interacoes, consultorias, parcelas, hoje)


def registrar_snapshots(db: Session, hoje: date | None = None) -> int:
    """Recalcula todos os clientes ativos e grava a foto do dia (substitui a do mesmo dia)."""
    hoje = hoje or date.today()
    clientes = db.scalars(select(Cliente).where(Cliente.status == "ativo")).all()
    for c in clientes:
        h = calcular_cliente(db, c, hoje)
        existente = db.scalar(
            select(HealthSnapshot).where(HealthSnapshot.cliente_id == c.id, HealthSnapshot.data == hoje)
        )
        snap = existente or HealthSnapshot(cliente_id=c.id, data=hoje)
        snap.score, snap.faixa = h.score, h.faixa
        snap.engajamento, snap.sentimento = h.engajamento, h.sentimento
        snap.financeiro, snap.resultado = h.financeiro, h.resultado
        snap.alertas = "\n".join(h.alertas)
        db.add(snap)
    db.commit()
    return len(clientes)
