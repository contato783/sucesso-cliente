"""Sponte: alunos/clientes e parcelas financeiras.

ATENÇÃO: não encontrei documentação pública da API do Sponte. A URL, os
nomes dos métodos e os nomes dos campos abaixo seguem o padrão do web
service WSAPIEdu (ASMX) e estão todos configuráveis no .env. Antes de
ativar, peça ao suporte do Sponte a documentação da sua conta e ajuste
SPONTE_METODO_* e, se preciso, os candidatos em CAMPOS_*.
"""

import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Any

import httpx

from app.config import Settings

# Para cada campo nosso, os nomes possíveis no XML do Sponte (sem diferenciar maiúsculas)
CAMPOS_ALUNO = {
    "id": ["AlunoID", "CodigoAluno", "ID"],
    "nome": ["Nome", "NomeAluno"],
    "telefone": ["Celular", "Telefone", "TelefoneCelular"],
    "situacao": ["Situacao", "SituacaoAluno"],
}
CAMPOS_PARCELA = {
    "id": ["ContaReceberID", "ParcelaID", "CodigoParcela", "ID"],
    "aluno_id": ["AlunoID", "CodigoAluno", "SacadoID"],
    "vencimento": ["Vencimento", "DataVencimento"],
    "valor": ["Valor", "ValorParcela", "ValorOriginal"],
    "data_pagamento": ["DataPagamento", "DataBaixa", "Pagamento"],
    "situacao": ["SituacaoParcela", "Situacao", "Status"],
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _registros(xml: str) -> list[dict[str, str]]:
    """Achata o XML: cada elemento cujos filhos são todos folhas vira um dicionário."""
    raiz = ET.fromstring(xml)
    saida = []
    for el in raiz.iter():
        filhos = list(el)
        if filhos and all(len(f) == 0 for f in filhos):
            saida.append({_local(f.tag): (f.text or "").strip() for f in filhos})
    return saida


def _campo(reg: dict[str, str], candidatos: list[str]) -> str | None:
    for c in candidatos:
        if reg.get(c.lower()):
            return reg[c.lower()]
    return None


def _data(valor: str | None) -> date | None:
    if not valor:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(valor[:19], fmt).date()
        except ValueError:
            continue
    return None


def _numero(valor: str | None) -> float:
    if not valor:
        return 0.0
    if "," in valor:
        valor = valor.replace(".", "").replace(",", ".")
    return float(valor)


def _chamar(s: Settings, metodo: str, busca: str = "") -> list[dict[str, str]]:
    if not (s.sponte_codigo_cliente and s.sponte_token):
        raise RuntimeError("Configure SPONTE_CODIGO_CLIENTE e SPONTE_TOKEN no .env")
    r = httpx.get(
        f"{s.sponte_base_url.rstrip('/')}/{metodo}",
        params={"nCodigoCliente": s.sponte_codigo_cliente, "sToken": s.sponte_token, "sParametrosBusca": busca},
        timeout=60,
    )
    r.raise_for_status()
    return _registros(r.text)


def parse_alunos(registros: list[dict[str, str]]) -> list[dict[str, Any]]:
    saida = []
    for reg in registros:
        sid = _campo(reg, CAMPOS_ALUNO["id"])
        if not sid:
            continue
        saida.append({
            "sponte_id": sid,
            "nome": _campo(reg, CAMPOS_ALUNO["nome"]) or f"Aluno {sid}",
            "telefone": _campo(reg, CAMPOS_ALUNO["telefone"]),
            "situacao": _campo(reg, CAMPOS_ALUNO["situacao"]),
        })
    return saida


def parse_parcelas(registros: list[dict[str, str]]) -> list[dict[str, Any]]:
    saida = []
    for reg in registros:
        pid, aluno = _campo(reg, CAMPOS_PARCELA["id"]), _campo(reg, CAMPOS_PARCELA["aluno_id"])
        vencimento = _data(_campo(reg, CAMPOS_PARCELA["vencimento"]))
        if not (pid and aluno and vencimento):
            continue
        pagamento = _data(_campo(reg, CAMPOS_PARCELA["data_pagamento"]))
        situacao = (_campo(reg, CAMPOS_PARCELA["situacao"]) or "").lower()
        saida.append({
            "sponte_parcela_id": pid,
            "aluno_id": aluno,
            "vencimento": vencimento,
            "valor": _numero(_campo(reg, CAMPOS_PARCELA["valor"])),
            "pago": pagamento is not None or situacao in ("paga", "pago", "quitada", "liquidada"),
            "data_pagamento": pagamento,
        })
    return saida


def buscar_alunos(s: Settings) -> list[dict[str, Any]]:
    return parse_alunos(_chamar(s, s.sponte_metodo_alunos))


def buscar_parcelas(s: Settings, aluno_id: str) -> list[dict[str, Any]]:
    return parse_parcelas(_chamar(s, s.sponte_metodo_parcelas, f"AlunoID={aluno_id}"))
