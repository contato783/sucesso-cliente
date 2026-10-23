"""Dados compartilhados pelas telas: carteira calculada e lista de prioridades do dia."""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import atendimento, health
from app.config import get_settings
from app.db import agora_utc
from app.models import Cliente, Interacao


@dataclass
class Linha:
    cliente: Cliente
    health: health.Health


@dataclass
class Acao:
    prioridade: int  # 1 = hoje, 2 = esta semana, 3 = quando der
    cliente: Cliente
    texto: str
    area: str
    lei: int  # qual das 10 leis a ação sustenta


def carregar(db: Session, hoje: date | None = None) -> list[Linha]:
    """Clientes ativos com o health calculado, do mais crítico ao mais saudável."""
    clientes = db.scalars(select(Cliente).where(Cliente.status == "ativo")).all()
    linhas = [Linha(c, health.calcular_cliente(db, c, hoje)) for c in clientes]
    return sorted(linhas, key=lambda l: l.health.score)


def atendimento_recente(db: Session, dias: int = 30) -> atendimento.Atendimento:
    desde = agora_utc() - timedelta(days=dias)
    interacoes = db.scalars(select(Interacao).where(Interacao.ocorrido_em >= desde)).all()
    return atendimento.calcular(list(interacoes), dias=dias)


def acoes(db: Session, linhas: list[Linha], atend: atendimento.Atendimento,
          hoje: date | None = None) -> list[Acao]:
    hoje = hoje or date.today()
    s = get_settings()
    por_id = {l.cliente.id: l for l in linhas}
    lista: list[Acao] = []

    for m in atend.sem_resposta:
        if m.cliente_id in por_id:
            horas = int((agora_utc() - m.ocorrido_em).total_seconds() // 3600)
            espera = f"{horas} h" if horas < 48 else f"{horas // 24} dias"
            lista.append(Acao(1, por_id[m.cliente_id].cliente,
                              f"Responder mensagem no {m.canal} parada há {espera}", "atendimento", 2))

    for l in linhas:
        c, h = l.cliente, l.health
        if h.faixa == "risco" and (h.dias_sem_toque is None or h.dias_sem_toque > 7):
            lista.append(Acao(1, c, "Em risco e sem contato na última semana: ligar e montar plano de ação",
                              "relacionamento", 4))
        if c.data_renovacao and 0 <= (c.data_renovacao - hoje).days <= 45 and h.faixa != "saudavel":
            lista.append(Acao(1, c, f"Renova em {(c.data_renovacao - hoje).days} dias com saúde "
                                    f"{health.faixa_do_score(h.score).replace('atencao', 'em atenção')}: "
                                    "preparar conversa mostrando os resultados", "comercial", 8))
        if h.dias_ate_primeiro_valor is None and h.dias_desde_inicio is not None \
                and h.dias_desde_inicio > s.meta_primeiro_valor_dias:
            lista.append(Acao(2, c, f"{h.dias_desde_inicio} dias sem primeiro resultado: "
                                    "definir uma vitória rápida para as próximas 2 semanas", "entrega", 7))
        if h.financeiro is not None and h.financeiro <= 50:
            lista.append(Acao(2, c, "Parcela atrasada: alinhar com o financeiro antes de cobrar", "financeiro", 10))
        if h.relacao_sem_valor:
            lista.append(Acao(2, c, "Gosta da gente, mas o resultado não está evidente: "
                                    "registrar e mostrar os ganhos", "entrega", 5))
        if h.faixa != "risco" and (h.dias_sem_toque is None or h.dias_sem_toque > s.meta_toque_dias):
            lista.append(Acao(3, c, "Sem contato proativo recente: mandar uma mensagem de acompanhamento",
                              "atendimento", 2))
        if not c.resultado_esperado:
            lista.append(Acao(3, c, "Registrar o resultado que o cliente espera alcançar", "entrega", 3))
        if not c.fit:
            lista.append(Acao(3, c, "Avaliar se o cliente está no perfil ideal (fit)", "comercial", 1))

    # Dentro da mesma prioridade, o cliente mais crítico primeiro
    return sorted(lista, key=lambda a: (a.prioridade, por_id[a.cliente.id].health.score))
