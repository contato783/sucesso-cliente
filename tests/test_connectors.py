from datetime import date, datetime, timedelta

from app import atendimento
from app.connectors import slack, sponte, whatsapp
from app.ingest import chave_telefone, salvar_interacao, vincular_orfas
from app.models import Cliente, Interacao


def test_meta_cloud_api():
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": "5511987654321", "profile": {"name": "Ana"}}],
        "messages": [{"from": "5511987654321", "id": "wamid.1", "timestamp": "1761200000",
                      "type": "text", "text": {"body": "Quero cancelar"}}],
    }}]}]}
    [m] = whatsapp.parse(payload)
    assert m["direcao"] == "entrada" and m["autor"] == "Ana" and m["texto"] == "Quero cancelar"
    assert m["external_id"] == "wa:wamid.1"


def test_evolution_grupo_e_fromme():
    payload = {"event": "messages.upsert", "data": {
        "key": {"remoteJid": "120363@g.us", "fromMe": True, "id": "ABC"},
        "message": {"conversation": "Bom dia!"}, "messageTimestamp": 1761200000}}
    [m] = whatsapp.parse(payload)
    assert m["direcao"] == "saida" and m["origem"] == "120363@g.us"


def test_zapi():
    payload = {"phone": "5511987654321", "messageId": "Z1", "fromMe": False, "momment": 1761200000000,
               "senderName": "Bruno", "text": {"message": "Obrigado pela consultoria"}}
    [m] = whatsapp.parse(payload)
    assert m["autor"] == "Bruno" and m["ocorrido_em"].year == 2025


def test_slack_connect_direcao():
    base = {"type": "event_callback", "team_id": "T_MEU"}
    cliente = slack.parse_evento({**base, "event": {"type": "message", "channel": "C1", "user": "U9",
                                                    "user_team": "T_CLIENTE", "text": "oi", "ts": "1761200000.1"}})
    equipe = slack.parse_evento({**base, "event": {"type": "message", "channel": "C1", "user": "U1",
                                                   "user_team": "T_MEU", "text": "olá", "ts": "1761200001.1"}})
    assert cliente["direcao"] == "entrada" and equipe["direcao"] == "saida"


def test_sponte_parse_parcelas():
    xml = """<?xml version="1.0"?><ArrayOfWSParcela xmlns="http://api.sponteeducacional.net.br/">
      <WSParcela><ContaReceberID>10</ContaReceberID><AlunoID>7</AlunoID>
        <Vencimento>2026-09-10T00:00:00</Vencimento><Valor>1.250,00</Valor><DataPagamento></DataPagamento>
        <SituacaoParcela>Pendente</SituacaoParcela></WSParcela>
      <WSParcela><ContaReceberID>11</ContaReceberID><AlunoID>7</AlunoID>
        <Vencimento>10/08/2026</Vencimento><Valor>1250.00</Valor><DataPagamento>12/08/2026</DataPagamento>
      </WSParcela></ArrayOfWSParcela>"""
    p1, p2 = sponte.parse_parcelas(sponte._registros(xml))
    assert p1["valor"] == 1250.0 and not p1["pago"] and p1["vencimento"] == date(2026, 9, 10)
    assert p2["pago"] and p2["data_pagamento"] == date(2026, 8, 12)


def test_telefone_sem_nono_digito_casa():
    assert chave_telefone("+55 (11) 98765-4321") == chave_telefone("551187654321")


def test_dedupe_e_vinculo_de_orfas(db):
    kw = dict(canal="whatsapp", direcao="entrada", texto="oi", ocorrido_em=datetime(2026, 10, 1),
              origem="5511987654321", external_id="wa:1")
    assert salvar_interacao(db, **kw) is not None
    assert salvar_interacao(db, **kw) is None
    c = Cliente(nome="Ana", telefone="5511987654321")
    db.add(c)
    db.commit()
    assert vincular_orfas(db, c) == 1


def test_tempo_de_resposta_e_sem_resposta():
    agora = datetime(2026, 10, 23, 12)
    def m(h, d, origem="A"):
        return Interacao(canal="whatsapp", direcao=d, origem=origem, texto="x",
                         ocorrido_em=agora - timedelta(hours=h))
    a = atendimento.calcular([m(10, "entrada"), m(9.5, "saida"), m(48, "entrada", "B")], agora)
    assert a.tempo_resposta_mediano_min == 30
    assert len(a.sem_resposta) == 1 and a.sem_resposta[0].origem == "B"
