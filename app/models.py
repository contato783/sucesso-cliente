from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, agora_utc



class Cliente(Base):
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(200))
    empresa: Mapped[str | None] = mapped_column(String(200))
    programa: Mapped[str | None] = mapped_column(String(100))  # PEP, MESP, Potencialize...
    responsavel_cs: Mapped[str | None] = mapped_column(String(100))
    # Chaves para casar mensagens de cada fonte com o cliente
    telefone: Mapped[str | None] = mapped_column(String(20), index=True)  # só dígitos, com DDI
    whatsapp_grupo_id: Mapped[str | None] = mapped_column(String(100), index=True)  # ...@g.us
    slack_channel_id: Mapped[str | None] = mapped_column(String(50), index=True)
    sponte_id: Mapped[str | None] = mapped_column(String(50), index=True)

    valor_mensal: Mapped[float | None] = mapped_column(Float)
    data_inicio: Mapped[date | None] = mapped_column(Date)
    data_renovacao: Mapped[date | None] = mapped_column(Date)
    progresso: Mapped[int | None] = mapped_column(Integer)  # 0-100, avanço no programa
    status: Mapped[str] = mapped_column(String(20), default="ativo")  # ativo | pausado | cancelado
    # Lei 1: o cliente certo
    fit: Mapped[str | None] = mapped_column(String(10))  # alto | medio | baixo
    vendedor: Mapped[str | None] = mapped_column(String(100))
    # Lei 3: o resultado que o cliente espera
    resultado_esperado: Mapped[str | None] = mapped_column(Text)
    # Lei 8: indicadores de receita e churn
    valor_inicial: Mapped[float | None] = mapped_column(Float)
    data_cancelamento: Mapped[date | None] = mapped_column(Date)
    motivo_cancelamento: Mapped[str | None] = mapped_column(String(100))
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora_utc)

    interacoes: Mapped[list["Interacao"]] = relationship(back_populates="cliente")
    consultorias: Mapped[list["Consultoria"]] = relationship(back_populates="cliente")
    parcelas: Mapped[list["Parcela"]] = relationship(back_populates="cliente")
    snapshots: Mapped[list["HealthSnapshot"]] = relationship(back_populates="cliente")
    marcos: Mapped[list["Marco"]] = relationship(back_populates="cliente")


class Interacao(Base):
    """Uma mensagem ou contato vindo de qualquer canal."""

    __tablename__ = "interacoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("clientes.id"), index=True)
    canal: Mapped[str] = mapped_column(String(20))  # whatsapp | slack | consultoria
    direcao: Mapped[str] = mapped_column(String(10))  # entrada (cliente) | saida (equipe)
    autor: Mapped[str | None] = mapped_column(String(200))
    origem: Mapped[str | None] = mapped_column(String(100))  # telefone ou canal, para casar depois
    texto: Mapped[str] = mapped_column(Text, default="")
    ocorrido_em: Mapped[datetime] = mapped_column(DateTime, index=True)
    external_id: Mapped[str | None] = mapped_column(String(200), unique=True)

    analisado: Mapped[bool] = mapped_column(Boolean, default=False)
    sentimento: Mapped[float | None] = mapped_column(Float)  # -1 (muito negativo) a 1
    categoria: Mapped[str | None] = mapped_column(String(50))
    sinal_risco: Mapped[bool] = mapped_column(Boolean, default=False)

    cliente: Mapped[Cliente | None] = relationship(back_populates="interacoes")


class Consultoria(Base):
    __tablename__ = "consultorias"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), index=True)
    data: Mapped[datetime] = mapped_column(DateTime)
    consultor: Mapped[str | None] = mapped_column(String(100))
    duracao_min: Mapped[int | None] = mapped_column(Integer)
    compareceu: Mapped[bool] = mapped_column(Boolean, default=True)
    tarefas_combinadas: Mapped[int] = mapped_column(Integer, default=0)
    tarefas_cumpridas: Mapped[int] = mapped_column(Integer, default=0)
    nota_cliente: Mapped[int | None] = mapped_column(Integer)  # 0-10, se coletada
    notas: Mapped[str | None] = mapped_column(Text)

    cliente: Mapped[Cliente] = relationship(back_populates="consultorias")


class Marco(Base):
    """Resultado comprovado do cliente (Leis 3 e 7)."""

    __tablename__ = "marcos"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), index=True)
    data: Mapped[date] = mapped_column(Date)
    tipo: Mapped[str] = mapped_column(String(20))  # primeiro_valor | meta | ganho
    descricao: Mapped[str] = mapped_column(Text)
    valor_gerado: Mapped[float | None] = mapped_column(Float)  # R$ que o cliente ganhou/economizou

    cliente: Mapped[Cliente] = relationship(back_populates="marcos")


class Parcela(Base):
    """Parcela financeira sincronizada do Sponte."""

    __tablename__ = "parcelas"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), index=True)
    sponte_parcela_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    vencimento: Mapped[date] = mapped_column(Date)
    valor: Mapped[float] = mapped_column(Float, default=0)
    pago: Mapped[bool] = mapped_column(Boolean, default=False)
    data_pagamento: Mapped[date | None] = mapped_column(Date)

    cliente: Mapped[Cliente] = relationship(back_populates="parcelas")


class HealthSnapshot(Base):
    """Foto diária do health score, para ver a evolução."""

    __tablename__ = "health_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), index=True)
    data: Mapped[date] = mapped_column(Date, index=True)
    score: Mapped[float] = mapped_column(Float)
    faixa: Mapped[str] = mapped_column(String(20))
    engajamento: Mapped[float | None] = mapped_column(Float)
    sentimento: Mapped[float | None] = mapped_column(Float)
    financeiro: Mapped[float | None] = mapped_column(Float)
    resultado: Mapped[float | None] = mapped_column(Float)
    alertas: Mapped[str] = mapped_column(Text, default="")  # separados por "\n"

    cliente: Mapped[Cliente] = relationship(back_populates="snapshots")
