from datetime import date

from fastapi.testclient import TestClient

from app import importar
from app.db import _ajustar_url
from app.main import app
from app.models import Cliente

PLANILHA_EXCEL = """Nome;Empresa;Responsável CS;WhatsApp;Valor mensal;Início;Fit;Resultado esperado
Maria Souza;Padaria Bom Pão;Carol;(11) 98765-4321;1.800,00;15/03/2026;Médio;Margem de 15%
João Lima;Auto Peças;Daniele;;R$ 2.500,50;2026-01-10;alto;
Sem Data;X;;;;31/02/2026;;
""".encode("latin-1")


def test_importa_planilha_do_excel(db):
    r = importar.importar(db, PLANILHA_EXCEL)
    assert (r.criados, r.atualizados) == (2, 0)
    assert len(r.erros) == 1 and "Linha 4" in r.erros[0]
    maria = db.query(Cliente).filter_by(nome="Maria Souza").one()
    assert maria.telefone == "5511987654321" and maria.valor_mensal == 1800 and maria.valor_inicial == 1800
    assert maria.data_inicio == date(2026, 3, 15) and maria.fit == "medio"
    joao = db.query(Cliente).filter_by(nome="João Lima").one()
    assert joao.valor_mensal == 2500.5


def test_reimportar_atualiza_sem_duplicar(db):
    importar.importar(db, PLANILHA_EXCEL)
    r = importar.importar(db, "nome,whatsapp,valor mensal\nMaria S.,11987654321,2000\n".encode())
    assert (r.criados, r.atualizados) == (0, 1)
    assert db.query(Cliente).count() == 2
    assert db.query(Cliente).filter_by(nome="Maria S.").one().valor_mensal == 2000


def test_planilha_sem_coluna_nome(db):
    r = importar.importar(db, b"empresa;valor\nX;10\n")
    assert r.erros and "Nome" in r.erros[0]


def test_url_do_render():
    assert _ajustar_url("postgres://u:s@h/db") == "postgresql+psycopg://u:s@h/db"
    assert _ajustar_url("postgresql://u:s@h/db") == "postgresql+psycopg://u:s@h/db"
    assert _ajustar_url("sqlite:///x.db") == "sqlite:///x.db"


def test_telas_de_importar_e_configuracoes(db):
    from app.db import get_db

    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    assert "Comece por aqui" in client.get("/").text
    assert client.get("/clientes/modelo.csv").text.startswith("﻿Nome;")
    r = client.post("/clientes/importar", files={"arquivo": ("c.csv", PLANILHA_EXCEL, "text/csv")})
    assert r.status_code == 200 and "2 cliente(s) novo(s)" in r.text
    pagina = client.get("/configuracoes")
    assert pagina.status_code == 200 and "/webhooks/whatsapp" in pagina.text
    assert "Comece por aqui" not in client.get("/").text
    app.dependency_overrides.clear()
