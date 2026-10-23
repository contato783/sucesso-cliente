"""Dados fictícios para conhecer o painel antes de conectar as fontes reais."""

import random
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app import health
from app.db import agora_utc
from app.models import Cliente, Consultoria, Interacao, Marco, Parcela

PERFIS = [
    # nome, programa, valor, dias sem falar, sentimento, atraso (dias), presença, progresso, fit, dias de casa, 1º resultado (dia)
    ("Padaria Bom Pão", "PEP", 1800, 1, 0.7, None, 1.0, 75, "alto", 200, 18),
    ("Clínica Sorriso", "MESP", 2400, 3, 0.4, None, 0.9, 60, "alto", 150, 25),
    ("Auto Peças Silva", "PEP", 1500, 20, -0.3, 18, 0.5, 30, "baixo", 120, None),
    ("Studio Bella", "Potencialize", 900, 6, 0.7, 4, 0.8, 35, "medio", 90, None),
    ("Construtora Alfa", "In Company", 6500, 35, -0.6, 45, 0.3, 15, "baixo", 240, 70),
    ("Mercado Família", "PEP", 1800, 2, 0.5, None, 1.0, 85, "alto", 420, 12),
    ("Ótica Visão", "MESP", 2200, 4, 0.5, None, 0.9, 55, "medio", 20, None),
    ("Pet Shop Amigo", "Potencialize", 1100, 9, 0.3, None, 0.8, 50, None, 60, 40),
]
CANCELADOS = [("Gráfica Rápida", "PEP", 1500, "baixo", "Não viu resultado", 40),
              ("Buffet Festa", "MESP", 2000, "medio", "Preço", 75)]


FRASES = {
    "pos": ["Adorei a consultoria de ontem!", "Já aplicamos o DRE, ficou muito mais claro", "Equipe engajada com o plano 90 dias"],
    "neu": ["Consegue mandar o material?", "Podemos remarcar para quinta?", "Recebi, obrigado"],
    "neg": ["Não estou conseguindo ver resultado", "Está puxado pagar esse valor agora", "Acho que vamos dar uma pausa"],
}


def popular(db: Session) -> int:
    random.seed(7)
    agora, hoje = agora_utc(), date.today()
    for i, (nome, prog, valor, sumido, sent, atraso, presenca, progresso, fit, casa, primeiro) in enumerate(PERFIS):
        c = Cliente(nome=nome, programa=prog, valor_mensal=valor, valor_inicial=valor * (0.8 if i % 3 == 0 else 1),
                    responsavel_cs=["Carol", "Daniele"][i % 2], vendedor=["Marcos", "Júlia"][i % 2], fit=fit,
                    telefone=f"55119{87650000 + i:08d}", progresso=progresso,
                    resultado_esperado="Organizar o financeiro e chegar a 15% de margem" if i % 4 != 3 else None,
                    data_inicio=hoje - timedelta(days=casa), data_renovacao=hoje + timedelta(days=15 + i * 12))
        db.add(c)
        db.flush()
        if primeiro is not None:
            db.add(Marco(cliente_id=c.id, data=c.data_inicio + timedelta(days=primeiro), tipo="primeiro_valor",
                         descricao="Primeiro fluxo de caixa montado"))
            if sent > 0.3 and presenca >= 0.9:
                db.add(Marco(cliente_id=c.id, data=hoje - timedelta(days=10 + i), tipo="ganho",
                             descricao="Redução de custos fixos", valor_gerado=valor * 4))
        for d in range(sumido, 60, 4):
            tom = "pos" if sent > 0.3 else ("neg" if sent < -0.2 and d < sumido + 10 else "neu")
            quando = agora - timedelta(days=d, hours=random.randint(0, 8))
            canal = random.choice(["whatsapp", "whatsapp", "slack"])
            db.add(Interacao(cliente_id=c.id, canal=canal,
                             direcao="entrada", origem=c.telefone, texto=random.choice(FRASES[tom]),
                             ocorrido_em=quando, analisado=True, categoria="outro",
                             sentimento=max(-1, min(1, sent + random.uniform(-0.2, 0.2))),
                             sinal_risco=tom == "neg" and d < 30))
            if d > 1:
                db.add(Interacao(cliente_id=c.id, canal=canal, direcao="saida", origem=c.telefone,
                                 texto="Combinado!", ocorrido_em=quando + timedelta(minutes=random.randint(5, 300)),
                                 analisado=True))
        for m in range(6):
            venc = hoje - timedelta(days=30 * m + 5)
            atrasada = atraso is not None and m == 0
            venc = hoje - timedelta(days=atraso) if atrasada else venc
            db.add(Parcela(cliente_id=c.id, vencimento=venc, valor=valor, pago=not atrasada,
                           data_pagamento=None if atrasada else venc, sponte_parcela_id=f"demo-{c.id}-{m}"))
        for k in range(6):
            db.add(Consultoria(cliente_id=c.id, data=agora - timedelta(days=14 * k + 2), consultor="Carol",
                               compareceu=random.random() < presenca, tarefas_combinadas=4,
                               tarefas_cumpridas=round(4 * presenca),
                               nota_cliente=max(0, min(10, round(7 + sent * 4))) if k == 0 else None))
    for nome, prog, valor, fit, motivo, dias in CANCELADOS:
        db.add(Cliente(nome=nome, programa=prog, valor_mensal=valor, valor_inicial=valor, fit=fit, status="cancelado",
                       vendedor="Marcos", data_inicio=hoje - timedelta(days=400),
                       data_cancelamento=hoje - timedelta(days=dias), motivo_cancelamento=motivo))
    db.commit()
    for dias in range(30, -1, -3):  # histórico para o gráfico de evolução
        health.registrar_snapshots(db, hoje - timedelta(days=dias))
    return len(PERFIS)
