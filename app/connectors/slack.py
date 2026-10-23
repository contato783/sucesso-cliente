"""Slack: Events API (tempo real) e histórico de canais (carga inicial).

Pensado para canais Slack Connect com cada cliente: mensagens de usuários
de outro workspace contam como "entrada" (cliente); as do seu workspace,
como "saida" (equipe).
"""

import hashlib
import hmac
import time
from datetime import UTC, datetime
from typing import Any

import httpx

API = "https://slack.com/api"
SUBTIPOS_IGNORADOS = {"channel_join", "channel_leave", "message_changed", "message_deleted", "bot_message"}


def assinatura_valida(corpo: bytes, timestamp: str | None, assinatura: str | None, secret: str) -> bool:
    if not secret:
        return True
    if not timestamp or not assinatura or abs(time.time() - int(timestamp)) > 300:
        return False
    base = f"v0:{timestamp}:{corpo.decode()}".encode()
    esperado = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, assinatura)


def normalizar(evento: dict[str, Any], meu_team_id: str | None) -> dict | None:
    if evento.get("type") != "message" or evento.get("subtype") in SUBTIPOS_IGNORADOS:
        return None
    if evento.get("bot_id"):
        return None
    time_do_autor = evento.get("user_team") or evento.get("team")
    externo = bool(meu_team_id and time_do_autor and time_do_autor != meu_team_id)
    return {
        "direcao": "entrada" if externo else "saida",
        "origem": evento.get("channel"),
        "autor": evento.get("user"),
        "texto": evento.get("text", ""),
        "ocorrido_em": datetime.fromtimestamp(float(evento["ts"]), UTC).replace(tzinfo=None),
        "external_id": f"slack:{evento.get('channel')}:{evento['ts']}",
    }


def parse_evento(payload: dict[str, Any]) -> dict | None:
    if payload.get("type") != "event_callback":
        return None
    return normalizar(payload.get("event", {}), payload.get("team_id"))


def _get(token: str, metodo: str, **params) -> dict:
    r = httpx.get(f"{API}/{metodo}", params=params, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    dados = r.json()
    if not dados.get("ok"):
        raise RuntimeError(f"Slack {metodo}: {dados.get('error')}")
    return dados


def meu_team_id(token: str) -> str:
    return _get(token, "auth.test")["team_id"]


def historico(token: str, canal: str, desde: datetime | None = None) -> list[dict]:
    """Mensagens de um canal (requer escopos channels:history / groups:history)."""
    team = meu_team_id(token)
    params: dict[str, Any] = {"channel": canal, "limit": 200}
    if desde:
        params["oldest"] = str(desde.timestamp())
    saida, cursor = [], None
    while True:
        if cursor:
            params["cursor"] = cursor
        dados = _get(token, "conversations.history", **params)
        for ev in dados.get("messages", []):
            ev.setdefault("channel", canal)
            ev.setdefault("type", "message")
            if (n := normalizar(ev, team)) is not None:
                saida.append(n)
        cursor = dados.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            return saida
