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

    assert client.get("/").status_code == 200
    pagina = client.get(f"/clientes/{cid}")
    assert pagina.status_code == 200 and "Não estou vendo resultado" in pagina.text

    [cli] = client.get("/api/clientes").json()
    assert cli["nome"] == "Padaria Bom Pão" and cli["resultado"] == 0

    assert client.post("/webhooks/slack", json={"type": "url_verification", "challenge": "abc"}).json() == {"challenge": "abc"}
    app.dependency_overrides.clear()
