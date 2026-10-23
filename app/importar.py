"""Importação de clientes a partir de uma planilha (CSV exportado do Excel ou Google Planilhas)."""

import csv
import io
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ingest import chave_telefone, normalizar_telefone, vincular_orfas
from app.models import Cliente

# Coluna da planilha -> campo do cliente. Os nomes são comparados sem acento e sem maiúsculas.
COLUNAS = {
    "nome": "nome", "cliente": "nome",
    "empresa": "empresa",
    "programa": "programa", "produto": "programa",
    "responsavel": "responsavel_cs", "responsavel cs": "responsavel_cs", "cs": "responsavel_cs",
    "telefone": "telefone", "whatsapp": "telefone", "celular": "telefone",
    "valor mensal": "valor_mensal", "valor": "valor_mensal", "mensalidade": "valor_mensal",
    "valor inicial": "valor_inicial",
    "inicio": "data_inicio", "data de inicio": "data_inicio", "inicio do contrato": "data_inicio",
    "renovacao": "data_renovacao", "data de renovacao": "data_renovacao",
    "fit": "fit", "perfil": "fit",
    "vendedor": "vendedor", "quem vendeu": "vendedor",
    "resultado esperado": "resultado_esperado", "objetivo": "resultado_esperado",
    "codigo sponte": "sponte_id", "sponte": "sponte_id",
    "canal slack": "slack_channel_id", "slack": "slack_channel_id",
    "grupo whatsapp": "whatsapp_grupo_id",
}
CABECALHO_MODELO = ["Nome", "Empresa", "Programa", "Responsável CS", "WhatsApp", "Valor mensal", "Início",
                    "Renovação", "Fit", "Vendedor", "Resultado esperado", "Código Sponte", "Canal Slack"]
EXEMPLO_MODELO = ["Maria Souza", "Padaria Bom Pão", "PEP", "Carol", "11 98765-4321", "1.800,00", "15/03/2026",
                  "15/03/2027", "alto", "Marcos", "Chegar a 15% de margem líquida", "", ""]


@dataclass
class Resultado:
    criados: int = 0
    atualizados: int = 0
    erros: list[str] = field(default_factory=list)


def _sem_acento(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(texto.lower().replace("_", " ").split())


def _data(valor: str) -> date | None:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(valor, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"data inválida: {valor}")


def _numero(valor: str) -> float:
    v = valor.replace("R$", "").strip()
    if "," in v:
        v = v.replace(".", "").replace(",", ".")
    return float(v)


def _fit(valor: str) -> str | None:
    v = _sem_acento(valor)
    for opcao in ("alto", "medio", "baixo"):
        if v.startswith(opcao):
            return opcao
    raise ValueError(f"fit deve ser alto, médio ou baixo (veio: {valor})")


CONVERSORES = {"valor_mensal": _numero, "valor_inicial": _numero, "data_inicio": _data, "data_renovacao": _data,
               "fit": _fit, "telefone": normalizar_telefone}


def ler(conteudo: bytes) -> list[dict[str, str]]:
    try:
        texto = conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = conteudo.decode("latin-1")  # Excel brasileiro antigo
    amostra = texto[:2048]
    delimitador = ";" if amostra.count(";") > amostra.count(",") else ","
    leitor = csv.DictReader(io.StringIO(texto), delimiter=delimitador)
    linhas = []
    for linha in leitor:
        convertida = {}
        for coluna, valor in linha.items():
            campo = COLUNAS.get(_sem_acento(coluna or ""))
            if campo and valor and valor.strip():
                convertida[campo] = valor.strip()
        linhas.append(convertida)
    return linhas


def _existente(db: Session, dados: dict) -> Cliente | None:
    """Evita duplicar: procura pelo código Sponte, depois pelo telefone, depois pelo nome."""
    if dados.get("sponte_id"):
        c = db.scalar(select(Cliente).where(Cliente.sponte_id == dados["sponte_id"]))
        if c:
            return c
    if dados.get("telefone"):
        alvo = chave_telefone(dados["telefone"])
        for c in db.scalars(select(Cliente).where(Cliente.telefone.is_not(None))):
            if chave_telefone(c.telefone) == alvo:
                return c
    return db.scalar(select(Cliente).where(func.lower(Cliente.nome) == dados["nome"].lower()))


def importar(db: Session, conteudo: bytes) -> Resultado:
    r = Resultado()
    linhas = ler(conteudo)
    if linhas and not any("nome" in l for l in linhas):
        r.erros.append("Não achei a coluna “Nome”. Use o modelo de planilha.")
        return r
    for numero, bruta in enumerate(linhas, start=2):  # linha 1 é o cabeçalho
        if not bruta.get("nome"):
            continue
        try:
            dados = {campo: CONVERSORES.get(campo, str)(valor) for campo, valor in bruta.items()}
        except ValueError as e:
            r.erros.append(f"Linha {numero} ({bruta['nome']}): {e}")
            continue
        cliente = _existente(db, dados)
        if cliente:
            r.atualizados += 1
        else:
            cliente = Cliente(status="ativo")
            db.add(cliente)
            r.criados += 1
        for campo, valor in dados.items():
            setattr(cliente, campo, valor)
        if cliente.valor_inicial is None:
            cliente.valor_inicial = cliente.valor_mensal
        db.flush()
        vincular_orfas(db, cliente)
    db.commit()
    return r


def modelo_csv() -> str:
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    escritor.writerow(CABECALHO_MODELO)
    escritor.writerow(EXEMPLO_MODELO)
    return "﻿" + saida.getvalue()  # BOM para o Excel abrir com acentos certos
