"""Comandos de linha:

    python -m app.cli init                 cria as tabelas
    python -m app.cli sync                 sincroniza tudo, analisa sentimento e recalcula scores
    python -m app.cli sponte [--criar]     só Sponte (--criar cadastra alunos novos como clientes)
    python -m app.cli slack [dias]         carrega histórico do Slack (padrão: 90 dias)
    python -m app.cli sentimento           só a análise de sentimento
    python -m app.cli score                só recalcula os health scores
    python -m app.cli demo                 popula dados fictícios para testar o painel
"""

import json
import logging
import sys

from app import demo, health, sentiment, sync
from app.db import SessionLocal, init_db


def main(args: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args:
        print(__doc__)
        return
    init_db()
    comando = args[0]
    with SessionLocal() as db:
        if comando == "init":
            resultado = "tabelas criadas"
        elif comando == "sync":
            resultado = sync.rodar_tudo(db)
        elif comando == "sponte":
            resultado = sync.sincronizar_sponte(db, criar_clientes="--criar" in args)
        elif comando == "slack":
            resultado = sync.sincronizar_slack(db, dias=int(args[1]) if len(args) > 1 else 90)
        elif comando == "sentimento":
            resultado = sentiment.analisar_pendentes(db)
        elif comando == "demo":
            resultado = {"clientes_demo": demo.popular(db)}
        elif comando == "score":
            resultado = health.registrar_snapshots(db)
        else:
            print(__doc__)
            sys.exit(1)
    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main(sys.argv[1:])
