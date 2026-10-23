"""As 10 leis do Customer Success (Nick Mehta) transformadas em indicadores.

Cada lei vira uma pergunta com resposta numérica, uma meta e um semáforo,
mais a lista de clientes que precisam de atenção por causa dela.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean, median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.atendimento import Atendimento
from app.config import get_settings
from app.health import AREAS
from app.models import Cliente, Consultoria, Marco
from app.painel import Linha

STATUS = {"ok": "No caminho", "atencao": "Atenção", "critico": "Crítico", "sem_dados": "Sem dados"}


@dataclass
class Lei:
    numero: int
    titulo: str
    significado: str
    pergunta: str  # o que o indicador responde
    valor: str = "—"
    meta: str = ""
    status: str = "sem_dados"
    detalhe: str = ""
    acao: str = ""  # o que fazer para melhorar
    tabela: list[tuple[str, str]] = field(default_factory=list)
    clientes: list[tuple[Cliente, str]] = field(default_factory=list)


def _pct(parte: float, total: float) -> float:
    return 100 * parte / total if total else 0.0


def _fmt_pct(v: float) -> str:
    return f"{v:.0f}%"


def _brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _semaforo(valor: float, ok: float, atencao: float, maior_melhor: bool = True) -> str:
    if maior_melhor:
        return "ok" if valor >= ok else ("atencao" if valor >= atencao else "critico")
    return "ok" if valor <= ok else ("atencao" if valor <= atencao else "critico")


def lei1_cliente_certo(linhas: list[Linha], todos: list[Cliente], hoje: date) -> Lei:
    lei = Lei(1, "Venda para o cliente certo",
              "Nem todo cliente é adequado ao produto. Uma venda mal alinhada gera insatisfação e cancelamento.",
              "Quanto da carteira está no perfil ideal (fit alto ou médio)?", meta="90% ou mais")
    avaliados = [l for l in linhas if l.cliente.fit]
    lei.clientes = [(l.cliente, "Fit baixo") for l in linhas if l.cliente.fit == "baixo"]
    lei.clientes += [(l.cliente, "Fit não avaliado") for l in linhas if not l.cliente.fit]
    lei.acao = "Avalie o fit de cada cliente no cadastro e leve os casos de fit baixo para o comercial revisar o perfil de venda."
    if not avaliados:
        lei.detalhe = "Nenhum cliente com fit avaliado ainda."
        return lei
    bons = sum(1 for l in avaliados if l.cliente.fit in ("alto", "medio"))
    pct = _pct(bons, len(avaliados))
    lei.valor, lei.status = _fmt_pct(pct), _semaforo(pct, 90, 75)
    lei.detalhe = f"{len(avaliados)} de {len(linhas)} clientes avaliados."
    for fit in ("alto", "medio", "baixo"):
        grupo = [l.health.score for l in avaliados if l.cliente.fit == fit]
        if grupo:
            lei.tabela.append((f"Score médio, fit {fit.replace('medio', 'médio')}", f"{mean(grupo):.0f} ({len(grupo)})"))
    cancelados = [c for c in todos if c.data_cancelamento and c.data_cancelamento >= hoje - timedelta(days=365)]
    if cancelados:
        baixos = sum(1 for c in cancelados if c.fit == "baixo")
        lei.tabela.append(("Cancelados em 12 meses que eram fit baixo", f"{baixos} de {len(cancelados)}"))
    por_vendedor = defaultdict(list)
    for c in todos:
        if c.vendedor and c.fit:
            por_vendedor[c.vendedor].append(c.fit == "baixo")
    for vendedor, baixos in sorted(por_vendedor.items()):
        lei.tabela.append((f"Fit baixo vendido por {vendedor}", _fmt_pct(_pct(sum(baixos), len(baixos)))))
    return lei


def lei2_distanciamento(linhas: list[Linha]) -> Lei:
    meta = get_settings().meta_toque_dias
    lei = Lei(2, "O distanciamento é natural",
              "Sem acompanhamento, cliente e empresa se afastam. A conexão precisa ser cultivada.",
              f"Quantos clientes receberam contato proativo da equipe nos últimos {meta} dias?",
              meta="90% ou mais")
    lei.acao = "Tenha uma cadência fixa de contato (mensagem de acompanhamento, check-in, consultoria) para cada cliente."
    if not linhas:
        return lei
    tocados = [l for l in linhas if l.health.dias_sem_toque is not None and l.health.dias_sem_toque <= meta]
    pct = _pct(len(tocados), len(linhas))
    lei.valor, lei.status = _fmt_pct(pct), _semaforo(pct, 90, 70)
    lei.detalhe = f"{len(tocados)} de {len(linhas)} clientes."
    sem = sorted((l for l in linhas if l not in tocados),
                 key=lambda l: -(l.health.dias_sem_toque if l.health.dias_sem_toque is not None else 9999))
    lei.clientes = [(l.cliente, f"{l.health.dias_sem_toque} dias sem contato" if l.health.dias_sem_toque is not None
                     else "Nenhum contato registrado") for l in sem]
    return lei


def lei3_resultado(linhas: list[Linha], marcos: list[Marco], hoje: date) -> Lei:
    lei = Lei(3, "O cliente espera resultados extraordinários",
              "Não basta entregar o que foi contratado. É preciso ajudá-lo a alcançar o resultado esperado.",
              "Quantos clientes têm resultado comprovado nos últimos 90 dias?", meta="70% ou mais")
    lei.acao = "Registre com cada cliente o resultado esperado e anote cada meta batida ou ganho na página dele."
    if not linhas:
        return lei
    comprovados = [l for l in linhas if l.health.resultado_comprovado]
    pct = _pct(len(comprovados), len(linhas))
    lei.valor, lei.status = _fmt_pct(pct), _semaforo(pct, 70, 40)
    definidos = sum(1 for l in linhas if l.cliente.resultado_esperado)
    lei.detalhe = f"{len(comprovados)} de {len(linhas)} clientes."
    lei.tabela.append(("Com resultado esperado definido", _fmt_pct(_pct(definidos, len(linhas)))))
    ganho = sum(m.valor_gerado or 0 for m in marcos if m.data >= hoje - timedelta(days=365))
    if ganho:
        lei.tabela.append(("Valor gerado para clientes em 12 meses", _brl(ganho)))
    for l in linhas:
        if not l.cliente.resultado_esperado:
            lei.clientes.append((l.cliente, "Resultado esperado não definido"))
        elif not l.health.resultado_comprovado:
            lei.clientes.append((l.cliente, "Sem resultado comprovado em 90 dias"))
    return lei


def lei4_saude(linhas: list[Linha]) -> Lei:
    lei = Lei(4, "Monitore a saúde do cliente",
              "Acompanhe continuamente comportamento, engajamento e sinais de risco.",
              "Dos clientes em risco ou atenção, quantos tiveram ação da equipe nos últimos 7 dias?",
              meta="90% ou mais")
    lei.acao = "Todo cliente que cair para atenção ou risco precisa de um contato e um plano de ação na mesma semana."
    if not linhas:
        return lei
    cobertos = sum(1 for l in linhas
                   if sum(v is not None for v in (l.health.engajamento, l.health.sentimento,
                                                  l.health.financeiro, l.health.resultado)) >= 3)
    lei.tabela.append(("Clientes com 3+ fontes de dados no score", _fmt_pct(_pct(cobertos, len(linhas)))))
    faixas = Counter(l.health.faixa for l in linhas)
    lei.tabela.append(("Saudáveis / atenção / risco", f"{faixas['saudavel']} / {faixas['atencao']} / {faixas['risco']}"))
    alerta = [l for l in linhas if l.health.faixa != "saudavel"]
    if not alerta:
        lei.valor, lei.status, lei.detalhe = "100%", "ok", "Nenhum cliente em atenção ou risco."
        return lei
    tratados = [l for l in alerta if l.health.dias_sem_toque is not None and l.health.dias_sem_toque <= 7]
    pct = _pct(len(tratados), len(alerta))
    lei.valor, lei.status = _fmt_pct(pct), _semaforo(pct, 90, 60)
    lei.detalhe = f"{len(tratados)} de {len(alerta)} clientes em atenção ou risco."
    lei.clientes = [(l.cliente, f"Score {l.health.score:.0f}, sem ação na última semana")
                    for l in alerta if l not in tratados]
    return lei


def lei5_relacionamento(linhas: list[Linha]) -> Lei:
    lei = Lei(5, "Relacionamento pessoal não garante fidelidade",
              "Ser querido pelo cliente não basta para mantê-lo. O valor entregue precisa ser evidente.",
              "Quantos clientes estão satisfeitos, mas sem resultado comprovado?", meta="Nenhum")
    lei.acao = "Para esses clientes, transforme a boa relação em evidência: números, antes e depois, metas batidas."
    if not linhas:
        return lei
    casos = [l for l in linhas if l.health.relacao_sem_valor]
    lei.valor = str(len(casos))
    lei.status = "ok" if not casos else _semaforo(_pct(len(casos), len(linhas)), 0, 10, maior_melhor=False)
    lei.detalhe = "Sentimento das conversas 65+ e nenhuma meta ou ganho registrado em 90 dias."
    lei.clientes = [(l.cliente, f"Sentimento {l.health.sentimento:.0f}, resultado "
                     f"{'sem dados' if l.health.resultado is None else f'{l.health.resultado:.0f}'}") for l in casos]
    return lei


def lei6_produto(linhas: list[Linha]) -> Lei:
    lei = Lei(6, "O produto é o diferencial escalável",
              "Uma experiência boa de forma consistente depende de produto e processos, não só de pessoas excepcionais.",
              "Qual a diferença de score médio entre os responsáveis de CS?", meta="Até 10 pontos")
    lei.acao = "Se um responsável vai muito melhor que outro, transforme o jeito dele em processo (playbook, roteiro, material)."
    por_cs = defaultdict(list)
    por_programa = defaultdict(list)
    for l in linhas:
        if l.cliente.responsavel_cs:
            por_cs[l.cliente.responsavel_cs].append(l.health.score)
        if l.cliente.programa:
            por_programa[l.cliente.programa].append(l.health.score)
    grupos = {cs: notas for cs, notas in por_cs.items() if len(notas) >= 2}
    for cs, notas in sorted(por_cs.items(), key=lambda x: -mean(x[1])):
        lei.tabela.append((f"CS {cs}", f"{mean(notas):.0f} ({len(notas)} clientes)"))
    for prog, notas in sorted(por_programa.items(), key=lambda x: -mean(x[1])):
        lei.tabela.append((f"Programa {prog}", f"{mean(notas):.0f} ({len(notas)} clientes)"))
    if len(grupos) < 2:
        lei.detalhe = "Precisa de pelo menos 2 responsáveis com 2+ clientes cada."
        return lei
    medias = [mean(n) for n in grupos.values()]
    diferenca = max(medias) - min(medias)
    lei.valor, lei.status = f"{diferenca:.0f} pts", _semaforo(diferenca, 10, 20, maior_melhor=False)
    lei.detalhe = "Diferença pequena indica que o resultado vem do processo, não da pessoa."
    return lei


def lei7_primeiro_valor(linhas: list[Linha]) -> Lei:
    meta = get_settings().meta_primeiro_valor_dias
    lei = Lei(7, "Reduza o tempo até o primeiro resultado",
              "Quanto mais rápido o cliente percebe valor, maiores as chances de seguir engajado.",
              "Quantos dias, em média, o cliente leva para ter o primeiro resultado?", meta=f"Até {meta} dias")
    lei.acao = "Planeje no onboarding uma vitória rápida e registre-a como primeiro resultado na página do cliente."
    tempos = [l.health.dias_ate_primeiro_valor for l in linhas if l.health.dias_ate_primeiro_valor is not None]
    atrasados = [l for l in linhas if l.health.dias_ate_primeiro_valor is None
                 and l.health.dias_desde_inicio is not None and l.health.dias_desde_inicio > meta]
    lei.clientes = [(l.cliente, f"{l.health.dias_desde_inicio} dias de contrato, sem primeiro resultado")
                    for l in sorted(atrasados, key=lambda l: -l.health.dias_desde_inicio)]
    if not tempos:
        lei.detalhe = "Nenhum resultado registrado ainda. Preencha a data de início e registre os resultados."
        return lei
    med = median(tempos)
    lei.valor = f"{med:.0f} dias"
    lei.status = "critico" if atrasados and len(atrasados) > len(tempos) else _semaforo(med, meta, meta * 1.5, maior_melhor=False)
    no_prazo = sum(1 for t in tempos if t <= meta)
    lei.detalhe = f"Mediana de {len(tempos)} clientes."
    lei.tabela = [("Primeiro resultado dentro da meta", _fmt_pct(_pct(no_prazo, len(tempos)))),
                  ("Atrasados (sem resultado após a meta)", str(len(atrasados)))]
    return lei


def lei8_indicadores(linhas: list[Linha], todos: list[Cliente], hoje: date) -> Lei:
    lei = Lei(8, "Conheça profundamente seus indicadores",
              "Churn, retenção, receita recorrente e comportamento precisam orientar a gestão.",
              "Quanto da receita recorrente foi perdida com cancelamentos nos últimos 90 dias?", meta="Até 5%")
    lei.acao = "Ao cancelar um cliente, registre data e motivo. Revise os motivos mais frequentes todo mês."
    mrr = sum(l.cliente.valor_mensal or 0 for l in linhas)
    cancelados = [c for c in todos if c.data_cancelamento and c.data_cancelamento >= hoje - timedelta(days=90)]
    perdido = sum(c.valor_mensal or 0 for c in cancelados)
    lei.tabela.append(("Receita recorrente mensal (MRR)", _brl(mrr)))
    lei.tabela.append(("Clientes ativos", str(len(linhas))))
    base = len(linhas) + len(cancelados)
    lei.tabela.append(("Churn de clientes (90 dias)", f"{_fmt_pct(_pct(len(cancelados), base))} ({len(cancelados)})"))
    coorte = [c for c in todos if c.data_inicio and c.data_inicio <= hoje - timedelta(days=365)
              and (c.data_cancelamento is None or c.data_cancelamento >= hoje - timedelta(days=365))]
    receita_inicial = sum(c.valor_inicial or c.valor_mensal or 0 for c in coorte)
    if receita_inicial:
        receita_hoje = sum(c.valor_mensal or 0 for c in coorte if c.status == "ativo")
        lei.tabela.append(("Retenção líquida de receita, 12 meses (NRR)", _fmt_pct(_pct(receita_hoje, receita_inicial))))
    motivos = Counter(c.motivo_cancelamento or "Não informado" for c in todos
                      if c.data_cancelamento and c.data_cancelamento >= hoje - timedelta(days=365))
    for motivo, n in motivos.most_common(3):
        lei.tabela.append((f"Motivo de cancelamento: {motivo}", str(n)))
    lei.clientes = [(c, f"Cancelou em {c.data_cancelamento:%d/%m}: {c.motivo_cancelamento or 'motivo não informado'}")
                    for c in sorted(cancelados, key=lambda c: c.data_cancelamento, reverse=True)]
    if not mrr and not perdido:
        lei.detalhe = "Preencha o valor mensal dos clientes para medir receita e churn."
        return lei
    pct = _pct(perdido, mrr + perdido)
    lei.valor, lei.status = _fmt_pct(pct), _semaforo(pct, 5, 10, maior_melhor=False)
    lei.detalhe = f"{_brl(perdido)} perdidos de {_brl(mrr + perdido)}."
    return lei


def lei9_metricas(linhas: list[Linha], consultorias: list[Consultoria], atend: Atendimento, hoje: date) -> Lei:
    lei = Lei(9, "Gerencie CS com métricas concretas",
              "Não basta dizer que os clientes estão felizes. É preciso demonstrar.",
              "Qual o NPS dos clientes nos últimos 90 dias?", meta="50 ou mais")
    lei.acao = "Peça a nota de 0 a 10 ao final das consultorias e registre. Detratores precisam de retorno em 48h."
    ativos = {l.cliente.id: l.cliente for l in linhas}
    ultima_nota: dict[int, int] = {}
    for c in sorted(consultorias, key=lambda c: c.data):
        if c.nota_cliente is not None and c.cliente_id in ativos and c.data.date() >= hoje - timedelta(days=90):
            ultima_nota[c.cliente_id] = c.nota_cliente
    if atend.respondidas_em_1h is not None:
        lei.tabela.append(("Mensagens respondidas em até 1h", _fmt_pct(atend.respondidas_em_1h * 100)))
    if atend.tempo_resposta_mediano_min is not None:
        t = atend.tempo_resposta_mediano_min
        lei.tabela.append(("Tempo mediano de resposta", f"{t:.0f} min" if t < 60 else f"{t / 60:.1f} h".replace(".", ",")))
    if linhas:
        comprovados = sum(1 for l in linhas if l.health.resultado_comprovado)
        lei.tabela.append(("Clientes com resultado comprovado", _fmt_pct(_pct(comprovados, len(linhas)))))
    if not ultima_nota:
        lei.detalhe = "Nenhuma nota registrada nas consultorias dos últimos 90 dias."
        return lei
    notas = list(ultima_nota.values())
    promotores = sum(1 for n in notas if n >= 9)
    detratores = sum(1 for n in notas if n <= 6)
    nps = _pct(promotores - detratores, len(notas))
    lei.valor, lei.status = f"{nps:.0f}", _semaforo(nps, 50, 0)
    lei.detalhe = f"{len(notas)} clientes responderam."
    lei.tabela.insert(0, ("Promotores / neutros / detratores",
                          f"{promotores} / {len(notas) - promotores - detratores} / {detratores}"))
    lei.clientes = [(ativos[cid], f"Nota {n}") for cid, n in ultima_nota.items() if n <= 6]
    return lei


def lei10_empresa(linhas: list[Linha], atend: Atendimento) -> Lei:
    lei = Lei(10, "Customer Success é responsabilidade de toda a empresa",
              "Comercial, produto, financeiro, operações e liderança precisam trabalhar pelo mesmo objetivo.",
              "De qual área vêm os riscos da carteira?", meta="Até 10% da carteira em risco")
    lei.acao = "Leve esta tabela para a reunião de liderança: cada área cuida dos riscos que nascem nela."
    if not linhas:
        return lei
    alerta = [l for l in linhas if l.health.faixa != "saudavel"]
    por_area: Counter = Counter()
    sem_resposta = {m.cliente_id for m in atend.sem_resposta}
    for l in alerta:
        areas = {a.area for a in l.health.alertas if hasattr(a, "area")}
        if l.cliente.id in sem_resposta:
            areas.add("atendimento")
        por_area.update(areas)
    lei.tabela = [(AREAS[a], f"{n} cliente(s)") for a, n in por_area.most_common()]
    em_risco = sum(1 for l in linhas if l.health.faixa == "risco")
    pct = _pct(em_risco, len(linhas))
    lei.status = _semaforo(pct, 10, 25, maior_melhor=False)
    if por_area:
        area, n = por_area.most_common(1)[0]
        lei.valor = AREAS[area]
        lei.detalhe = f"{n} dos {len(alerta)} clientes em atenção ou risco têm alerta desta área. {_fmt_pct(pct)} da carteira em risco."
    else:
        lei.valor, lei.detalhe = "Nenhuma", "Nenhum cliente em atenção ou risco."
    return lei


def calcular(db: Session, linhas: list[Linha], atend: Atendimento, hoje: date | None = None) -> list[Lei]:
    hoje = hoje or date.today()
    todos = list(db.scalars(select(Cliente)))
    marcos = list(db.scalars(select(Marco)))
    consultorias = list(db.scalars(select(Consultoria)))
    return [
        lei1_cliente_certo(linhas, todos, hoje),
        lei2_distanciamento(linhas),
        lei3_resultado(linhas, marcos, hoje),
        lei4_saude(linhas),
        lei5_relacionamento(linhas),
        lei6_produto(linhas),
        lei7_primeiro_valor(linhas),
        lei8_indicadores(linhas, todos, hoje),
        lei9_metricas(linhas, consultorias, atend, hoje),
        lei10_empresa(linhas, atend),
    ]
