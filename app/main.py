import asyncio
import json
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import atendimento, health, importar, leis, painel, sync
from app.config import get_settings
from app.connectors import slack, whatsapp
from app.db import SessionLocal, agora_utc, get_db, init_db
from app.ingest import normalizar_telefone, salvar_interacao, vincular_orfas
from app.models import Cliente, Consultoria, HealthSnapshot, Interacao, Marco, Parcela

log = logging.getLogger(__name__)


async def _sincronizar_periodicamente(minutos: int) -> None:
    """Roda a sincronização de tempos em tempos, sem precisar de agendador externo."""
    while True:
        await asyncio.sleep(minutos * 60)
        try:
            await asyncio.to_thread(_sync_em_segundo_plano)
        except Exception:  # uma falha não pode parar as próximas rodadas
            log.exception("Falha na sincronização automática")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    minutos = get_settings().sync_automatico_minutos
    tarefa = asyncio.create_task(_sincronizar_periodicamente(minutos)) if minutos > 0 else None
    yield
    if tarefa:
        tarefa.cancel()


app = FastAPI(title="Sucesso do Cliente", lifespan=lifespan)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["brl"] = lambda v: (
    "—" if v is None else f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
)
basic = HTTPBasic(auto_error=False)

FAIXAS = {"saudavel": "Saudável", "atencao": "Atenção", "risco": "Risco"}
TIPOS_MARCO = {"primeiro_valor": "Primeiro resultado", "meta": "Meta atingida", "ganho": "Ganho mensurável"}
templates.env.globals.update(FAIXAS=FAIXAS, TIPOS_MARCO=TIPOS_MARCO, AREAS=health.AREAS, STATUS_LEI=leis.STATUS)


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


class MarcoIn(BaseModel):
    cliente_id: int
    data: date
    tipo: str = "meta"  # primeiro_valor | meta | ganho
    descricao: str
    valor_gerado: float | None = None


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


@app.post("/api/marcos", dependencies=[Depends(autenticado)])
def api_marco(dados: MarcoIn, db: Session = Depends(get_db)):
    if not db.get(Cliente, dados.cliente_id):
        raise HTTPException(404, "Cliente não encontrado")
    if dados.tipo not in TIPOS_MARCO:
        raise HTTPException(422, f"tipo deve ser um de: {', '.join(TIPOS_MARCO)}")
    m = Marco(**dados.model_dump())
    db.add(m)
    db.commit()
    return {"id": m.id}


@app.get("/api/leis", dependencies=[Depends(autenticado)])
def api_leis(db: Session = Depends(get_db)):
    """As 10 leis em JSON, para levar a uma planilha ou reunião."""
    linhas = painel.carregar(db)
    return [{"numero": l.numero, "titulo": l.titulo, "pergunta": l.pergunta, "valor": l.valor, "meta": l.meta,
             "status": l.status, "detalhe": l.detalhe, "indicadores": dict(l.tabela),
             "clientes": [{"id": c.id, "nome": c.nome, "motivo": m} for c, m in l.clientes]}
            for l in leis.calcular(db, linhas, painel.atendimento_recente(db))]


@app.get("/api/clientes", dependencies=[Depends(autenticado)])
def api_clientes(db: Session = Depends(get_db)):
    saida = []
    for c in db.scalars(select(Cliente).where(Cliente.status == "ativo")):
        h = health.calcular_cliente(db, c)
        saida.append({"id": c.id, "nome": c.nome, "empresa": c.empresa, "programa": c.programa,
                      "score": h.score, "faixa": h.faixa, "engajamento": h.engajamento,
                      "sentimento": h.sentimento, "financeiro": h.financeiro, "resultado": h.resultado,
                      "alertas": [{"texto": str(a), "area": a.area} for a in h.alertas]})
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

def _404():
    raise HTTPException(404)


def _vazio(v: str | None):
    return v.strip() if v and v.strip() else None


def _numero(v: str | None) -> float | None:
    v = _vazio(v)
    if v is None:
        return None
    return float(v.replace(".", "").replace(",", ".")) if "," in v else float(v)


def _integracoes(request: Request, db: Session) -> list[dict]:
    """Situação de cada fonte de dados, com o que falta fazer e os endereços para colar."""
    s = get_settings()
    base = str(request.base_url).rstrip("/")

    def ultima(canal: str):
        return db.scalar(select(func.max(Interacao.ocorrido_em)).where(Interacao.canal == canal))

    def status(configurado: bool, recebendo: bool = True) -> str:
        return "ok" if configurado and recebendo else ("atencao" if configurado else "pendente")

    clientes = db.scalar(select(func.count()).select_from(Cliente)) or 0
    pendentes = db.scalar(select(func.count()).select_from(Interacao).where(
        Interacao.analisado.is_(False), Interacao.cliente_id.is_not(None))) or 0
    wa, sl = ultima("whatsapp"), ultima("slack")
    parcelas = db.scalar(select(func.count()).select_from(Parcela)) or 0
    token_wa = f"?token={s.whatsapp_webhook_secret}" if s.whatsapp_webhook_secret else ""
    return [
        {"nome": "Clientes", "status": "ok" if clientes else "pendente",
         "situacao": f"{clientes} cadastrado(s)" if clientes else "Nenhum cliente ainda",
         "passo": "Importe sua planilha de clientes ou cadastre um por um.",
         "link": ("/clientes/importar", "Importar planilha")},
        {"nome": "Senha do painel", "status": status(bool(s.painel_token)),
         "situacao": "Protegido por senha" if s.painel_token else "Sem senha: qualquer pessoa com o link entra",
         "passo": "Defina PAINEL_TOKEN nas variáveis do servidor. Ao entrar, use qualquer usuário e essa senha."},
        {"nome": "Claude (sentimento)", "status": status(bool(s.anthropic_api_key)),
         "situacao": (f"Configurado · {pendentes} mensagem(ns) aguardando análise" if s.anthropic_api_key
                      else "Sem chave: as conversas entram, mas sem leitura de sentimento"),
         "passo": "Crie uma chave em console.anthropic.com e cole em ANTHROPIC_API_KEY."},
        {"nome": "WhatsApp", "status": status(bool(s.whatsapp_verify_token or s.whatsapp_webhook_secret), wa is not None),
         "situacao": f"Última mensagem: {wa:%d/%m %H:%M}" if wa else "Nenhuma mensagem recebida ainda",
         "passo": "Cole o endereço abaixo no seu provedor de WhatsApp (Meta, Evolution API ou Z-API).",
         "enderecos": [("Meta Cloud API · URL de callback", f"{base}/webhooks/whatsapp"),
                       ("Meta Cloud API · token de verificação", s.whatsapp_verify_token or "defina WHATSAPP_VERIFY_TOKEN"),
                       ("Evolution API ou Z-API · URL do webhook", f"{base}/webhooks/whatsapp{token_wa}")]},
        {"nome": "Slack", "status": status(bool(s.slack_bot_token and s.slack_signing_secret), sl is not None),
         "situacao": f"Última mensagem: {sl:%d/%m %H:%M}" if sl else "Nenhuma mensagem recebida ainda",
         "passo": "Crie o app do Slack, preencha SLACK_BOT_TOKEN e SLACK_SIGNING_SECRET e cole o endereço em Event Subscriptions.",
         "enderecos": [("Event Subscriptions · Request URL", f"{base}/webhooks/slack")]},
        {"nome": "Sponte (financeiro)", "status": status(bool(s.sponte_codigo_cliente and s.sponte_token), parcelas > 0),
         "situacao": f"{parcelas} parcela(s) sincronizada(s)" if parcelas else "Nenhuma parcela sincronizada",
         "passo": "Peça ao suporte do Sponte o código de cliente, o token e a documentação da API."},
        {"nome": "Sincronização automática", "status": "ok" if s.sync_automatico_minutos > 0 else "atencao",
         "situacao": (f"A cada {s.sync_automatico_minutos} minutos" if s.sync_automatico_minutos > 0
                      else "Desligada: use o botão Sincronizar"),
         "passo": "Controlada por SYNC_AUTOMATICO_MINUTOS."},
    ]


@app.get("/configuracoes", response_class=HTMLResponse, dependencies=[Depends(autenticado)])
def configuracoes(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "configuracoes.html", {"itens": _integracoes(request, db)})


@app.get("/clientes/importar", response_class=HTMLResponse, dependencies=[Depends(autenticado)])
def importar_pagina(request: Request):
    return templates.TemplateResponse(request, "importar.html", {"resultado": None})


@app.get("/clientes/modelo.csv", dependencies=[Depends(autenticado)])
def importar_modelo():
    return Response(importar.modelo_csv(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="modelo-clientes.csv"'})


@app.post("/clientes/importar", response_class=HTMLResponse, dependencies=[Depends(autenticado)])
async def importar_enviar(request: Request, arquivo: UploadFile = File(...), db: Session = Depends(get_db)):
    resultado = importar.importar(db, await arquivo.read())
    return templates.TemplateResponse(request, "importar.html", {"resultado": resultado})


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(autenticado)])
def hoje(request: Request, db: Session = Depends(get_db)):
    """Tela inicial: o que fazer hoje e como está a carteira nas 10 leis."""
    linhas = painel.carregar(db)
    atend = painel.atendimento_recente(db)
    lista = painel.acoes(db, linhas, atend)
    contagem = {f: sum(1 for l in linhas if l.health.faixa == f) for f in FAIXAS}
    return templates.TemplateResponse(request, "hoje.html", {
        "linhas": linhas, "contagem": contagem, "acoes": lista,
        "score_medio": round(sum(l.health.score for l in linhas) / len(linhas), 1) if linhas else None,
        "mrr": sum(l.cliente.valor_mensal or 0 for l in linhas),
        "mrr_risco": sum(l.cliente.valor_mensal or 0 for l in linhas if l.health.faixa == "risco"),
        "leis": leis.calcular(db, linhas, atend),
        "orfas": db.scalar(select(func.count(func.distinct(Interacao.origem))).where(Interacao.cliente_id.is_(None))),
        "integracoes": _integracoes(request, db),
    })


@app.get("/carteira", response_class=HTMLResponse, dependencies=[Depends(autenticado)])
def carteira(request: Request, db: Session = Depends(get_db), faixa: str = "", cs: str = "", programa: str = "",
             busca: str = ""):
    todas = painel.carregar(db)
    linhas = [l for l in todas
              if (not faixa or l.health.faixa == faixa)
              and (not cs or l.cliente.responsavel_cs == cs)
              and (not programa or l.cliente.programa == programa)
              and (not busca or busca.lower() in f"{l.cliente.nome} {l.cliente.empresa or ''}".lower())]
    return templates.TemplateResponse(request, "carteira.html", {
        "linhas": linhas, "total": len(todas),
        "contagem": {f: sum(1 for l in todas if l.health.faixa == f) for f in FAIXAS},
        "filtros": {"faixa": faixa, "cs": cs, "programa": programa, "busca": busca},
        "opcoes_cs": sorted({l.cliente.responsavel_cs for l in todas if l.cliente.responsavel_cs}),
        "opcoes_programa": sorted({l.cliente.programa for l in todas if l.cliente.programa}),
        "hoje": date.today(),
    })


@app.get("/atendimento", response_class=HTMLResponse, dependencies=[Depends(autenticado)])
def atendimento_pagina(request: Request, db: Session = Depends(get_db)):
    nomes = {c.id: c.nome for c in db.scalars(select(Cliente))}
    return templates.TemplateResponse(request, "atendimento.html", {
        "atend": painel.atendimento_recente(db), "nomes": nomes,
    })


@app.get("/leis", response_class=HTMLResponse, dependencies=[Depends(autenticado)])
def leis_pagina(request: Request, db: Session = Depends(get_db)):
    linhas = painel.carregar(db)
    return templates.TemplateResponse(request, "leis.html", {
        "leis": leis.calcular(db, linhas, painel.atendimento_recente(db)),
    })


@app.get("/clientes/novo", response_class=HTMLResponse, dependencies=[Depends(autenticado)])
def cliente_novo(request: Request):
    return templates.TemplateResponse(request, "cliente_form.html", {"c": None})


@app.get("/clientes/{cid}/editar", response_class=HTMLResponse, dependencies=[Depends(autenticado)])
def cliente_editar(cid: int, request: Request, db: Session = Depends(get_db)):
    c = db.get(Cliente, cid) or _404()
    return templates.TemplateResponse(request, "cliente_form.html", {"c": c})


@app.post("/clientes/salvar", dependencies=[Depends(autenticado)])
def cliente_salvar(
    db: Session = Depends(get_db),
    id: int | None = Form(None), nome: str = Form(...), empresa: str = Form(""), programa: str = Form(""),
    responsavel_cs: str = Form(""), telefone: str = Form(""), whatsapp_grupo_id: str = Form(""),
    slack_channel_id: str = Form(""), sponte_id: str = Form(""), valor_mensal: str = Form(""),
    valor_inicial: str = Form(""), data_inicio: str = Form(""), data_renovacao: str = Form(""),
    progresso: str = Form(""), status: str = Form("ativo"), fit: str = Form(""), vendedor: str = Form(""),
    resultado_esperado: str = Form(""), motivo_cancelamento: str = Form(""), data_cancelamento: str = Form(""),
):
    c = db.get(Cliente, id) if id else Cliente()
    c.nome, c.empresa, c.programa = nome.strip(), _vazio(empresa), _vazio(programa)
    c.responsavel_cs, c.vendedor = _vazio(responsavel_cs), _vazio(vendedor)
    c.fit = fit if fit in ("alto", "medio", "baixo") else None
    c.resultado_esperado = _vazio(resultado_esperado)
    c.telefone = normalizar_telefone(telefone)
    c.whatsapp_grupo_id, c.slack_channel_id, c.sponte_id = (
        _vazio(whatsapp_grupo_id), _vazio(slack_channel_id), _vazio(sponte_id))
    c.valor_mensal = _numero(valor_mensal)
    # O primeiro valor vira a base para medir expansão/retenção de receita (Lei 8)
    c.valor_inicial = _numero(valor_inicial) or c.valor_inicial or c.valor_mensal
    c.data_inicio = date.fromisoformat(data_inicio) if _vazio(data_inicio) else None
    c.data_renovacao = date.fromisoformat(data_renovacao) if _vazio(data_renovacao) else None
    c.progresso = max(0, min(100, int(progresso))) if _vazio(progresso) else None
    c.status = status
    if status == "cancelado":
        c.data_cancelamento = (date.fromisoformat(data_cancelamento) if _vazio(data_cancelamento)
                               else c.data_cancelamento or date.today())
        c.motivo_cancelamento = _vazio(motivo_cancelamento)
    else:
        c.data_cancelamento = c.motivo_cancelamento = None
    db.add(c)
    db.commit()
    vincular_orfas(db, c)
    return RedirectResponse(f"/clientes/{c.id}", status_code=303)


@app.get("/clientes/{cid}", response_class=HTMLResponse, dependencies=[Depends(autenticado)])
def cliente_detalhe(cid: int, request: Request, db: Session = Depends(get_db)):
    c = db.get(Cliente, cid) or _404()
    h = health.calcular_cliente(db, c)
    interacoes = db.scalars(
        select(Interacao).where(Interacao.cliente_id == cid).order_by(Interacao.ocorrido_em.desc())
    ).all()
    atend = atendimento.calcular(list(interacoes), dias=90)
    linha = painel.Linha(c, h)
    return templates.TemplateResponse(request, "cliente.html", {
        "c": c, "h": h, "atend": atend,
        "acoes": painel.acoes(db, [linha], atend) if c.status == "ativo" else [],
        "interacoes": interacoes[:100],
        "consultorias": db.scalars(select(Consultoria).where(Consultoria.cliente_id == cid)
                                   .order_by(Consultoria.data.desc())).all(),
        "parcelas": db.scalars(select(Parcela).where(Parcela.cliente_id == cid)
                               .order_by(Parcela.vencimento.desc())).all(),
        "marcos": db.scalars(select(Marco).where(Marco.cliente_id == cid).order_by(Marco.data.desc())).all(),
        "historico": db.scalars(select(HealthSnapshot).where(HealthSnapshot.cliente_id == cid)
                                .order_by(HealthSnapshot.data.desc()).limit(90)).all()[::-1],
        "hoje": date.today(),
        "meta_toque": get_settings().meta_toque_dias,
        "meta_primeiro_valor": get_settings().meta_primeiro_valor_dias,
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
    return RedirectResponse(f"/clientes/{cid}#consultorias", status_code=303)


@app.post("/clientes/{cid}/marcos", dependencies=[Depends(autenticado)])
def marco_salvar(cid: int, db: Session = Depends(get_db), data: str = Form(...), tipo: str = Form("meta"),
                 descricao: str = Form(...), valor_gerado: str = Form("")):
    db.get(Cliente, cid) or _404()
    db.add(Marco(cliente_id=cid, data=date.fromisoformat(data), tipo=tipo if tipo in TIPOS_MARCO else "meta",
                 descricao=descricao.strip(), valor_gerado=_numero(valor_gerado)))
    db.commit()
    return RedirectResponse(f"/clientes/{cid}#resultados", status_code=303)


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
