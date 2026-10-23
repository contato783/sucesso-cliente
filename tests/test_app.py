from fastapi.testclient import TestClient

from app.main import app


def test_fluxo_completo(db):
    from app.db import get_db

    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    r = client.post("/clientes/salvar", data={"nome": "Padaria Bom Pão", "telefone": "11 98765-4321",
                                              "valor_mensal": "1.500,00", "programa": "PEP"},
                    follow_redirects=False)
    assert r.status_code == 303
    cid = int(r.headers["location"].rsplit("/", 1)[1])

    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "messages": [{"from": "5511987654321", "id": "wamid.X", "timestamp": "1761200000",
                      "type": "text", "text": {"body": "Não estou vendo resultado"}}]}}]}]}
    assert client.post("/webhooks/whatsapp", json=payload).json() == {"novas": 1}
    assert client.post("/webhooks/whatsapp", json=payload).json() == {"novas": 0}

    r = client.post(f"/clientes/{cid}/consultorias", data={"data": "2026-10-20T10:00", "compareceu": "nao",
                                                           "notas": "Cliente desmarcou de última hora"},
                    follow_redirects=False)
    assert r.status_code == 303

    for pagina in ("/", "/carteira", "/carteira?faixa=risco&busca=padaria", "/atendimento", "/leis", "/pendentes",
                   f"/clientes/{cid}/editar", "/clientes/novo"):
        assert client.get(pagina).status_code == 200, pagina

    r = client.post(f"/clientes/{cid}/marcos", data={"data": "2026-10-01", "tipo": "primeiro_valor",
                                                     "descricao": "Primeiro DRE", "valor_gerado": "1.000,00"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "Primeiro DRE" in client.get(f"/clientes/{cid}").text

    pagina = client.get(f"/clientes/{cid}")
    assert pagina.status_code == 200 and "Não estou vendo resultado" in pagina.text

    [cli] = client.get("/api/clientes").json()
    assert cli["nome"] == "Padaria Bom Pão" and cli["resultado"] == 0
    assert all("area" in a for a in cli["alertas"])

    r = client.post("/clientes/salvar", data={"id": cid, "nome": "Padaria Bom Pão", "status": "cancelado",
                                              "motivo_cancelamento": "Preço"}, follow_redirects=False)
    assert r.status_code == 303
    assert len(client.get("/api/leis").json()) == 10
    assert client.get("/api/clientes").json() == []
    lei8 = client.get("/api/leis").json()[7]
    assert lei8["clientes"][0]["motivo"].endswith("Preço")

    assert client.post("/webhooks/slack", json={"type": "url_verification", "challenge": "abc"}).json() == {"challenge": "abc"}
    app.dependency_overrides.clear()
