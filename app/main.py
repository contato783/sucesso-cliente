import json
from contextlib import asynccontextmanager
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import atendimento, health, sync
from app.config import get_settings
from app.connectors import slack, whatsapp
from app.db import SessionLocal, agora_utc, get_db, init_db
from app.ingest import normalizar_telefone, salvar_interacao, vincular_orfas
from app.models import Cliente, Consultoria, HealthSnapshot, Interacao, Parcela

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Sucesso do Cliente", lifespan=lifespan)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["brl"] = lambda v: (
    "—" if v is None else f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
)
basic = HTTPBasic(auto_error=False)

FAIXAS = {"saudavel": "Saudável", "atencao": "Atenção", "risco": "Risco"}
templates.env.globals["FAIXAS"] = FAIXAS


def autenticado(cred: HTTPBasicCredentials | None = Depends(basic)) -> None:
    """Painel e API protegidos por senha (PAINEL_TOKEN); usuário pode ser qualquer um."""
    token = get_settings().painel_token
    if not token:
        return
    if cred is None or not secrets.compare_digest(cred.password, token):
        raise HTTPException(401, "Senha inválida", headers={"WWW-Authenticate": "Basic"})


def _ingerir(db: Session, canal: str, mensagens: list[dict]) -> int:
    return sum(1 for m in mensagens if salvar_interacao(db, canal=canal, **m))


# ---------------------------------------------------------------- webhooks

@app.get("/webhooks/whatsapp")
def whatsapp_verificar(request: Request):
    """Verificação do webhook da Meta Cloud API."""
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == get_settings().whatsapp_verify_token:
        return PlainTextResponse(p.get("hub.challenge", ""))
    raise HTTPException(403)


@app.post("/webhooks/whatsapp")
async def whatsapp_receber(request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    corpo = await request.body()
    payload = json.loads(corpo or b"{}")
    if payload.get("object") == "whatsapp_business_account":
        if not whatsapp.assinatura_meta_valida(corpo, request.headers.get("x-hub-signature-256"), s.whatsapp_app_secret):
            raise HTTPException(401, "Assinatura inválida")
    elif s.whatsapp_webhook_secret:
        enviado = (request.headers.get("apikey") or request.headers.get("x-webhook-token")
                   or request.headers.get("client-token") or request.query_params.get("token") or "")
        if not secrets.compare_digest(enviado, s.whatsapp_webhook_secret):
            raise HTTPException(401, "Token inválido")
    return {"novas": _ingerir(db, "whatsapp", whatsapp.parse(payload))}


@app.post("/webhooks/slack")
async def slack_receber(request: Request, db: Session = Depends(get_db)):
    corpo = await request.body()
    if not slack.assinatura_valida(corpo, request.headers.get("x-slack-request-timestamp"),
                                   request.headers.get("x-slack-signature"), get_settings().slack_signing_secret):
        raise HTTPException(401, "Assinatura inválida")
    payload = json.loads(corpo or b"{}")
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}
    m = slack.parse_evento(payload)
    return {"novas": _ingerir(db, "slack", [m]) if m else 0}


# ---------------------------------------------------------------- API JSON

class InteracaoIn(BaseModel):
    canal: str
    direcao: str = "entrada"
    texto: str
    origem: str | None = None
    cliente_id: int | None = None
    autor: str | None = None
    ocorrido_em: datetime | None = None
    external_id: str | None = None


class ConsultoriaIn(BaseModel):
    cliente_id: int
    data: datetime
    consultor: str | None = None
    duracao_min: int | None = None
    compareceu: bool = True
    tarefas_combinadas: int = 0
    tarefas_cumpridas: int = 0
    nota_cliente: int | None = None
    notas: str | None = None


@app.post("/api/interacoes", dependencies=[Depends(autenticado)])
def api_interacao(dados: InteracaoIn, db: Session = Depends(get_db)):
    """Entrada genérica para qualquer outra fonte (formulário, e-mail, automação)."""
    i = salvar_interacao(db, canal=dados.canal, direcao=dados.direcao, texto=dados.texto, origem=dados.origem,
                         autor=dados.autor, ocorrido_em=dados.ocorrido_em or agora_utc(),
                         external_id=dados.external_id)
    if i and dados.cliente_id:
        i.cliente_id = dados.cliente_id
        db.commit()
    return {"id": i.id if i else None, "duplicada": i is None}


@app.post("/api/consultorias", dependencies=[Depends(autenticado)])
def api_consultoria(dados: ConsultoriaIn, db: Session = Depends(get_db)):
    if not db.get(Cliente, dados.cliente_id):
        raise HTTPException(404, "Cliente não encontrado")
    c = _registrar_consultoria(db, Consultoria(**dados.model_dump()))
    return {"id": c.id}


@app.get("/api/clientes", dependencies=[Depends(autenticado)])
def api_clientes(db: Session = Depends(get_db)):
    saida = []
    for c in db.scalars(select(Cliente).where(Cliente.status == "ativo")):
        h = health.calcular_cliente(db, c)
        saida.append({"id": c.id, "nome": c.nome, "empresa": c.empresa, "programa": c.programa,
                      "score": h.score, "faixa": h.faixa, "engajamento": h.engajamento,
                      "sentimento": h.sentimento, "financeiro": h.financeiro, "resultado": h.resultado,
                      "alertas": h.alertas})
    return sorted(saida, key=lambda x: x["score"])


def _registrar_consultoria(db: Session, c: Consultoria) -> Consultoria:
    db.add(c)
    db.flush()
    if c.notas:
        # As notas entram na linha do tempo e na análise de sentimento
        db.add(Interacao(cliente_id=c.cliente_id, canal="consultoria", direcao="entrada",
                         autor=c.consultor, texto=c.notas, ocorrido_em=c.data,
                         external_id=f"consultoria:{c.id}"))
    db.commit()
    return c


# ---------------------------------------------------------------- painel

def _carteira(db: Session) -> list[dict]:
    linhas = []
    for c in db.scalars(select(Cliente).where(Cliente.status == "ativo")):
        linhas.append({"cliente": c, "health": health.calcular_cliente(db, c)})
    return sorted(linhas, key=lambda x: x["health"].score)


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(autenticado)])
def painel(request: Request, db: Session = Depends(get_db)):
    carteira = _carteira(db)
    contagem = {f: sum(1 for l in carteira if l["health"].faixa == f) for f in FAIXAS}
    mrr_risco = sum(l["cliente"].valor_mensal or 0 for l in carteira if l["health"].faixa == "risco")
    score_medio = round(sum(l["health"].score for l in carteira) / len(carteira), 1) if carteira else None
    desde = agora_utc() - timedelta(days=30)
    interacoes = db.scalars(select(Interacao).where(Interacao.ocorrido_em >= desde)).all()
    nomes = {c.id: c.nome for c in db.scalars(select(Cliente))}
    orfas = db.scalar(select(func.count(func.distinct(Interacao.origem))).where(Interacao.cliente_id.is_(None)))
    renovacoes = sorted(
        (l for l in carteira if l["cliente"].data_renovacao
         and 0 <= (l["cliente"].data_renovacao - date.today()).days <= 60),
        key=lambda l: l["cliente"].data_renovacao,
    )
    return templates.TemplateResponse(request, "painel.html", {
        "carteira": carteira, "contagem": contagem, "mrr_risco": mrr_risco, "score_medio": score_medio,
        "atend": atendimento.calcular(list(interacoes)), "nomes": nomes, "orfas": orfas,
        "renovacoes": renovacoes,
    })


@app.get("/clientes/novo", response_class=HTMLResponse, dependencies=[Depends(autenticado)])
def cliente_novo(request: Request):
    return templates.TemplateResponse(request, "cliente_form.html", {"c": None})


@app.get("/clientes/{cid}/editar", response_class=HTMLResponse, dependencies=[Depends(autenticado)])
def cliente_editar(cid: int, request: Request, db: Session = Depends(get_db)):
    c = db.get(Cliente, cid) or _404()
    return templates.TemplateResponse(request, "cliente_form.html", {"c": c})


def _404():
    raise HTTPException(404)


def _vazio(v: str | None):
    return v.strip() if v and v.strip() else None


@app.post("/clientes/salvar", dependencies=[Depends(autenticado)])
def cliente_salvar(
    db: Session = Depends(get_db),
    id: int | None = Form(None), nome: str = Form(...), empresa: str = Form(""), programa: str = Form(""),
    responsavel_cs: str = Form(""), telefone: str = Form(""), whatsapp_grupo_id: str = Form(""),
    slack_channel_id: str = Form(""), sponte_id: str = Form(""), valor_mensal: str = Form(""),
    data_inicio: str = Form(""), data_renovacao: str = Form(""), progresso: str = Form(""),
    status: str = Form("ativo"),
):
    c = db.get(Cliente, id) if id else Cliente()
    c.nome, c.empresa, c.programa = nome.strip(), _vazio(empresa), _vazio(programa)
    c.responsavel_cs, c.status = _vazio(responsavel_cs), status
    c.telefone = normalizar_telefone(telefone)
    c.whatsapp_grupo_id, c.slack_channel_id, c.sponte_id = (
        _vazio(whatsapp_grupo_id), _vazio(slack_channel_id), _vazio(sponte_id))
    c.valor_mensal = float(valor_mensal.replace(".", "").replace(",", ".")) if _vazio(valor_mensal) else None
    c.data_inicio = date.fromisoformat(data_inicio) if _vazio(data_inicio) else None
    c.data_renovacao = date.fromisoformat(data_renovacao) if _vazio(data_renovacao) else None
    c.progresso = max(0, min(100, int(progresso))) if _vazio(progresso) else None
    db.add(c)
    db.commit()
    vincular_orfas(db, c)
    return RedirectResponse(f"/clientes/{c.id}", status_code=303)


@app.get("/clientes/{cid}", response_class=HTMLResponse, dependencies=[Depends(autenticado)])
def cliente_detalhe(cid: int, request: Request, db: Session = Depends(get_db)):
    c = db.get(Cliente, cid) or _404()
    interacoes = db.scalars(
        select(Interacao).where(Interacao.cliente_id == cid).order_by(Interacao.ocorrido_em.desc())
    ).all()
    return templates.TemplateResponse(request, "cliente.html", {
        "c": c,
        "h": health.calcular_cliente(db, c),
        "atend": atendimento.calcular(list(interacoes), dias=90),
        "interacoes": interacoes[:100],
        "consultorias": db.scalars(select(Consultoria).where(Consultoria.cliente_id == cid)
                                   .order_by(Consultoria.data.desc())).all(),
        "parcelas": db.scalars(select(Parcela).where(Parcela.cliente_id == cid)
                               .order_by(Parcela.vencimento.desc())).all(),
        "historico": db.scalars(select(HealthSnapshot).where(HealthSnapshot.cliente_id == cid)
                                .order_by(HealthSnapshot.data.desc()).limit(90)).all()[::-1],
        "hoje": date.today(),
    })


@app.post("/clientes/{cid}/consultorias", dependencies=[Depends(autenticado)])
def consultoria_salvar(
    cid: int, db: Session = Depends(get_db),
    data: str = Form(...), consultor: str = Form(""), duracao_min: str = Form(""),
    compareceu: str = Form("sim"), tarefas_combinadas: int = Form(0), tarefas_cumpridas: int = Form(0),
    nota_cliente: str = Form(""), notas: str = Form(""),
):
    db.get(Cliente, cid) or _404()
    _registrar_consultoria(db, Consultoria(
        cliente_id=cid, data=datetime.fromisoformat(data), consultor=_vazio(consultor),
        duracao_min=int(duracao_min) if _vazio(duracao_min) else None, compareceu=compareceu == "sim",
        tarefas_combinadas=tarefas_combinadas, tarefas_cumpridas=tarefas_cumpridas,
        nota_cliente=int(nota_cliente) if _vazio(nota_cliente) else None, notas=_vazio(notas),
    ))
    return RedirectResponse(f"/clientes/{cid}", status_code=303)


@app.get("/pendentes", response_class=HTMLResponse, dependencies=[Depends(autenticado)])
def pendentes(request: Request, db: Session = Depends(get_db)):
    """Contatos que mandaram mensagem mas ainda não estão ligados a um cliente."""
    linhas = db.execute(
        select(Interacao.canal, Interacao.origem, func.count(), func.max(Interacao.ocorrido_em),
               func.max(Interacao.autor))
        .where(Interacao.cliente_id.is_(None))
        .group_by(Interacao.canal, Interacao.origem)
        .order_by(func.max(Interacao.ocorrido_em).desc())
    ).all()
    clientes = db.scalars(select(Cliente).order_by(Cliente.nome)).all()
    return templates.TemplateResponse(request, "pendentes.html", {"linhas": linhas, "clientes": clientes})


@app.post("/pendentes/vincular", dependencies=[Depends(autenticado)])
def pendentes_vincular(db: Session = Depends(get_db), canal: str = Form(...), origem: str = Form(...),
                       cliente_id: int = Form(...)):
    c = db.get(Cliente, cliente_id) or _404()
    if canal == "slack":
        c.slack_channel_id = origem
    elif origem.endswith("@g.us"):
        c.whatsapp_grupo_id = origem
    else:
        c.telefone = normalizar_telefone(origem)
    db.commit()
    vincular_orfas(db, c)
    return RedirectResponse("/pendentes", status_code=303)


def _sync_em_segundo_plano() -> None:
    with SessionLocal() as db:
        sync.rodar_tudo(db)


@app.post("/sync", dependencies=[Depends(autenticado)])
def sincronizar(tarefas: BackgroundTasks):
    tarefas.add_task(_sync_em_segundo_plano)
    return RedirectResponse("/?sync=1", status_code=303)


@app.get("/saude")
def saude() -> Response:
    return PlainTextResponse("ok")
