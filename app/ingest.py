"""Grava interações vindas dos conectores e casa cada uma com um cliente."""

import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Cliente, Interacao


def normalizar_telefone(valor: str | None) -> str | None:
    if not valor:
        return None
    digitos = re.sub(r"\D", "", valor.split("@")[0])  # remove sufixo @s.whatsapp.net
    if len(digitos) in (10, 11):  # sem DDI: assume Brasil
        digitos = "55" + digitos
    return digitos or None


def chave_telefone(telefone: str | None) -> str | None:
    """DDI + DDD + 8 últimos dígitos.

    O WhatsApp às vezes entrega números brasileiros sem o nono dígito, então
    comparar pelos 8 finais evita perder o vínculo com o cliente.
    """
    t = normalizar_telefone(telefone)
    if not t or len(t) < 12:
        return t
    return t[:4] + t[-8:]


def achar_cliente(db: Session, canal: str, origem: str | None) -> Cliente | None:
    if not origem:
        return None
    if canal == "whatsapp" and origem.endswith("@g.us"):
        return db.scalar(select(Cliente).where(Cliente.whatsapp_grupo_id == origem))
    if canal == "whatsapp":
        alvo = chave_telefone(origem)
        for c in db.scalars(select(Cliente).where(Cliente.telefone.is_not(None))):
            if chave_telefone(c.telefone) == alvo:
                return c
        return None
    if canal == "slack":
        return db.scalar(select(Cliente).where(Cliente.slack_channel_id == origem))
    return None


def salvar_interacao(
    db: Session,
    *,
    canal: str,
    direcao: str,
    texto: str,
    ocorrido_em: datetime,
    origem: str | None,
    autor: str | None = None,
    external_id: str | None = None,
) -> Interacao | None:
    """Retorna a interação criada, ou None se já existia (mesmo external_id)."""
    if external_id and db.scalar(select(Interacao.id).where(Interacao.external_id == external_id)):
        return None
    cliente = achar_cliente(db, canal, origem)
    interacao = Interacao(
        cliente_id=cliente.id if cliente else None,
        canal=canal,
        direcao=direcao,
        texto=texto or "",
        ocorrido_em=ocorrido_em,
        origem=origem,
        autor=autor,
        external_id=external_id,
        # Mensagens da equipe não entram na análise de sentimento do cliente
        analisado=direcao == "saida" or not (texto or "").strip(),
    )
    db.add(interacao)
    db.commit()
    return interacao


def vincular_orfas(db: Session, cliente: Cliente) -> int:
    """Depois de cadastrar/editar um cliente, puxa as mensagens que chegaram antes do cadastro."""
    orfas = db.scalars(select(Interacao).where(Interacao.cliente_id.is_(None))).all()
    n = 0
    for i in orfas:
        if achar_cliente(db, i.canal, i.origem) is cliente:
            i.cliente_id = cliente.id
            n += 1
    db.commit()
    return n
