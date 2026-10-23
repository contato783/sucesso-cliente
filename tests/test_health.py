from datetime import date, datetime, timedelta

from app import health
from app.models import Cliente, Consultoria, Interacao, Parcela

HOJE = date(2026, 10, 23)


def msg(dias_atras, direcao="entrada", sentimento=None, risco=False):
    return Interacao(canal="whatsapp", direcao=direcao, texto="oi", origem="5511999990000",
                     ocorrido_em=datetime.combine(HOJE, datetime.min.time()) - timedelta(days=dias_atras),
                     analisado=sentimento is not None, sentimento=sentimento, sinal_risco=risco)


def test_cliente_engajado_e_adimplente_e_saudavel():
    c = Cliente(nome="A", progresso=80)
    interacoes = [msg(d, sentimento=0.6) for d in range(0, 30, 3)]
    parcelas = [Parcela(vencimento=HOJE - timedelta(days=30), valor=1000, pago=True,
                        data_pagamento=HOJE - timedelta(days=30))]
    consultorias = [Consultoria(data=datetime(2026, 10, 10), compareceu=True, tarefas_combinadas=4,
                                tarefas_cumpridas=4)]
    h = health.calcular(c, interacoes, consultorias, parcelas, HOJE)
    assert h.faixa == "saudavel"
    assert h.financeiro == 100


def test_inadimplencia_sumico_e_churn_viram_risco():
    c = Cliente(nome="B")
    interacoes = [msg(40, sentimento=-0.7, risco=True), msg(1, direcao="saida")]
    parcelas = [Parcela(vencimento=HOJE - timedelta(days=40), valor=1500, pago=False)]
    consultorias = [Consultoria(data=datetime(2026, 9, 1), compareceu=False),
                    Consultoria(data=datetime(2026, 10, 1), compareceu=False)]
    h = health.calcular(c, interacoes, consultorias, parcelas, HOJE)
    assert h.faixa == "risco"
    assert h.financeiro == 0
    assert any("parcela" in a for a in h.alertas)
    assert any("Faltou" in a for a in h.alertas)
    assert any("40 dias" in a for a in h.alertas)


def test_pilar_sem_dados_nao_pune():
    c = Cliente(nome="C")
    parcelas = [Parcela(vencimento=HOJE - timedelta(days=10), valor=500, pago=True,
                        data_pagamento=HOJE - timedelta(days=10))]
    h = health.calcular(c, [], [], parcelas, HOJE)
    assert h.engajamento is None and h.sentimento is None and h.resultado is None
    assert h.score == 100


def test_alerta_de_renovacao():
    c = Cliente(nome="D", data_renovacao=HOJE + timedelta(days=20))
    h = health.calcular(c, [], [], [], HOJE)
    assert "Renovação em 20 dias" in h.alertas


def test_snapshot_grava_um_por_dia(db):
    c = Cliente(nome="E", progresso=50)
    db.add(c)
    db.commit()
    health.registrar_snapshots(db, HOJE)
    health.registrar_snapshots(db, HOJE)
    from app.models import HealthSnapshot
    assert db.query(HealthSnapshot).count() == 1
