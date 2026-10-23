from datetime import date, datetime, timedelta

from app import demo, leis, painel
from app.atendimento import Atendimento
from app.health import Alerta, calcular
from app.models import Cliente, Consultoria, Interacao, Marco

HOJE = date.today()


def _ontem():
    return datetime.combine(HOJE - timedelta(days=1), datetime.min.time())


def test_alertas_tem_area():
    c = Cliente(nome="X", fit="baixo", data_renovacao=HOJE + timedelta(days=10))
    h = calcular(c, [], [], [], HOJE)
    areas = {a.area for a in h.alertas}
    assert {"comercial", "atendimento"} <= areas
    assert all(isinstance(a, Alerta) for a in h.alertas)


def test_tempo_ate_primeiro_valor_e_resultado_comprovado():
    c = Cliente(nome="X", data_inicio=HOJE - timedelta(days=60))
    marcos = [Marco(data=HOJE - timedelta(days=40), tipo="primeiro_valor", descricao="a"),
              Marco(data=HOJE - timedelta(days=5), tipo="ganho", descricao="b")]
    h = calcular(c, [], [], [], HOJE, marcos)
    assert h.dias_ate_primeiro_valor == 20
    assert h.resultado_comprovado


def test_sem_primeiro_valor_apos_meta_gera_alerta():
    c = Cliente(nome="X", data_inicio=HOJE - timedelta(days=45))
    h = calcular(c, [], [], [], HOJE)
    assert any("primeiro resultado" in a for a in h.alertas)


def test_relacao_boa_sem_valor():
    c = Cliente(nome="X", progresso=20)
    msgs = [Interacao(canal="whatsapp", direcao="entrada", texto="amei", ocorrido_em=_ontem(),
                      analisado=True, sentimento=0.8)]
    h = calcular(c, msgs, [], [], HOJE)
    assert h.relacao_sem_valor


def test_toque_proativo_conta_consultoria():
    c = Cliente(nome="X")
    h = calcular(c, [], [Consultoria(data=_ontem(), compareceu=True)], [], HOJE)
    assert h.dias_sem_toque == 1


def test_nps():
    c1, c2, c3 = Cliente(id=1, nome="a"), Cliente(id=2, nome="b"), Cliente(id=3, nome="c")
    linhas = [painel.Linha(c, calcular(c, [], [], [], HOJE)) for c in (c1, c2, c3)]
    consultorias = [Consultoria(cliente_id=1, data=_ontem(), nota_cliente=10),
                    Consultoria(cliente_id=2, data=_ontem(), nota_cliente=9),
                    Consultoria(cliente_id=3, data=_ontem(), nota_cliente=3)]
    lei = leis.lei9_metricas(linhas, consultorias, Atendimento(), HOJE)
    assert lei.valor == "33"  # (2 promotores - 1 detrator) / 3
    assert [c.nome for c, _ in lei.clientes] == ["c"]


def test_churn_de_receita():
    ativo = Cliente(nome="a", valor_mensal=900, status="ativo")
    cancelado = Cliente(nome="b", valor_mensal=100, status="cancelado", data_cancelamento=HOJE - timedelta(days=10))
    linhas = [painel.Linha(ativo, calcular(ativo, [], [], [], HOJE))]
    lei = leis.lei8_indicadores(linhas, [ativo, cancelado], HOJE)
    assert lei.valor == "10%" and lei.status == "atencao"


def test_dez_leis_com_dados_demo(db):
    demo.popular(db)
    linhas = painel.carregar(db)
    resultado = leis.calcular(db, linhas, painel.atendimento_recente(db))
    assert [l.numero for l in resultado] == list(range(1, 11))
    assert all(l.status in leis.STATUS for l in resultado)
    assert sum(l.status != "sem_dados" for l in resultado) >= 8
    assert painel.acoes(db, linhas, painel.atendimento_recente(db))
