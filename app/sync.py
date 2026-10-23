"""Rotinas de sincronização. Rode periodicamente com ``python -m app.cli sync``."""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import health, sentiment
from app.config import get_settings
from app.db import agora_utc
from app.connectors import slack, sponte
from app.ingest import normalizar_telefone, salvar_interacao
from app.models import Cliente, Parcela

log = logging.getLogger(__name__)


def sincronizar_sponte(db: Session, criar_clientes: bool = False) -> dict:
    s = get_settings()
    alunos = sponte.buscar_alunos(s)
    criados = 0
    for a in alunos:
        cliente = db.scalar(select(Cliente).where(Cliente.sponte_id == a["sponte_id"]))
        if cliente is None and criar_clientes:
            cliente = Cliente(nome=a["nome"], sponte_id=a["sponte_id"], telefone=normalizar_telefone(a["telefone"]))
            db.add(cliente)
            criados += 1
    db.commit()

    parcelas = 0
    for cliente in db.scalars(select(Cliente).where(Cliente.sponte_id.is_not(None))).all():
        for p in sponte.buscar_parcelas(s, cliente.sponte_id):
            reg = db.scalar(select(Parcela).where(Parcela.sponte_parcela_id == p["sponte_parcela_id"]))
            reg = reg or Parcela(cliente_id=cliente.id, sponte_parcela_id=p["sponte_parcela_id"])
            reg.vencimento, reg.valor = p["vencimento"], p["valor"]
            reg.pago, reg.data_pagamento = p["pago"], p["data_pagamento"]
            db.add(reg)
            parcelas += 1
        db.commit()
    return {"alunos": len(alunos), "clientes_criados": criados, "parcelas": parcelas}


def sincronizar_slack(db: Session, dias: int = 90) -> int:
    """Carga do histórico dos canais vinculados (o tempo real vem pelo webhook)."""
    token = get_settings().slack_bot_token
    if not token:
        raise RuntimeError("Configure SLACK_BOT_TOKEN no .env")
    desde = agora_utc() - timedelta(days=dias)
    novas = 0
    for cliente in db.scalars(select(Cliente).where(Cliente.slack_channel_id.is_not(None))).all():
        for m in slack.historico(token, cliente.slack_channel_id, desde):
            if salvar_interacao(db, canal="slack", **m):
                novas += 1
    return novas


def rodar_tudo(db: Session) -> dict:
    s = get_settings()
    resumo: dict = {}
    if s.sponte_codigo_cliente and s.sponte_token:
        try:
            resumo["sponte"] = sincronizar_sponte(db)
        except Exception as e:  # uma fonte fora do ar não deve travar as outras
            log.exception("Falha no Sponte")
            resumo["sponte"] = f"erro: {e}"
    if s.slack_bot_token:
        try:
            resumo["slack"] = sincronizar_slack(db, dias=2)
        except Exception as e:
            log.exception("Falha no Slack")
            resumo["slack"] = f"erro: {e}"
    resumo["sentimento_analisadas"] = sentiment.analisar_pendentes(db)
    resumo["clientes_recalculados"] = health.registrar_snapshots(db)
    return resumo
