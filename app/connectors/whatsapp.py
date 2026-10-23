"""Webhooks do WhatsApp.

Aceita três formatos, para você usar o provedor que já tem:
- Meta WhatsApp Cloud API (oficial)
- Evolution API (evento messages.upsert)
- Z-API (webhook "ao receber" e "ao enviar")

Cada função recebe o JSON do webhook e devolve uma lista de mensagens
normalizadas, prontas para ``ingest.salvar_interacao``.
"""

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any
from app.db import agora_utc


def _ts(segundos: float | int | str | None) -> datetime:
    if segundos in (None, ""):
        return agora_utc()
    valor = float(segundos)
    if valor > 1e12:  # milissegundos
        valor /= 1000
    return datetime.fromtimestamp(valor, tz=timezone.utc).replace(tzinfo=None)


def assinatura_meta_valida(corpo: bytes, assinatura: str | None, app_secret: str) -> bool:
    if not app_secret:
        return True
    if not assinatura or not assinatura.startswith("sha256="):
        return False
    esperado = hmac.new(app_secret.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, assinatura.removeprefix("sha256="))


def _texto_meta(msg: dict) -> str:
    tipo = msg.get("type")
    if tipo == "text":
        return msg.get("text", {}).get("body", "")
    if tipo in ("image", "video", "document"):
        return msg.get(tipo, {}).get("caption") or f"[{tipo}]"
    if tipo == "button":
        return msg.get("button", {}).get("text", "")
    if tipo == "interactive":
        inter = msg.get("interactive", {})
        return (inter.get("button_reply") or inter.get("list_reply") or {}).get("title", "")
    return f"[{tipo}]"


def parse_meta(payload: dict[str, Any]) -> list[dict]:
    saida = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            valor = change.get("value", {})
            nomes = {c.get("wa_id"): c.get("profile", {}).get("name") for c in valor.get("contacts", [])}
            for msg in valor.get("messages", []):
                saida.append({
                    "direcao": "entrada",
                    "origem": msg.get("from"),
                    "autor": nomes.get(msg.get("from")),
                    "texto": _texto_meta(msg),
                    "ocorrido_em": _ts(msg.get("timestamp")),
                    "external_id": f"wa:{msg.get('id')}",
                })
            # Coexistência com o app WhatsApp Business: mensagens enviadas pela equipe
            for msg in valor.get("message_echoes", []):
                saida.append({
                    "direcao": "saida",
                    "origem": msg.get("to"),
                    "autor": "equipe",
                    "texto": _texto_meta(msg),
                    "ocorrido_em": _ts(msg.get("timestamp")),
                    "external_id": f"wa:{msg.get('id')}",
                })
    return saida


def parse_evolution(payload: dict[str, Any]) -> list[dict]:
    if payload.get("event") not in ("messages.upsert", "MESSAGES_UPSERT"):
        return []
    dados = payload.get("data", {})
    itens = dados if isinstance(dados, list) else [dados]
    saida = []
    for d in itens:
        key = d.get("key", {})
        jid = key.get("remoteJid", "")
        if jid == "status@broadcast":
            continue
        msg = d.get("message") or {}
        texto = (
            msg.get("conversation")
            or msg.get("extendedTextMessage", {}).get("text")
            or msg.get("imageMessage", {}).get("caption")
            or msg.get("videoMessage", {}).get("caption")
            or ("[audio]" if "audioMessage" in msg else "")
        )
        de_mim = bool(key.get("fromMe"))
        saida.append({
            "direcao": "saida" if de_mim else "entrada",
            "origem": jid if jid.endswith("@g.us") else jid.split("@")[0],
            "autor": "equipe" if de_mim else d.get("pushName"),
            "texto": texto,
            "ocorrido_em": _ts(d.get("messageTimestamp")),
            "external_id": f"wa:{key.get('id')}",
        })
    return saida


def parse_zapi(payload: dict[str, Any]) -> list[dict]:
    if "phone" not in payload or "messageId" not in payload:
        return []
    de_mim = bool(payload.get("fromMe"))
    texto = (
        (payload.get("text") or {}).get("message")
        or (payload.get("image") or {}).get("caption")
        or ("[audio]" if payload.get("audio") else "")
    )
    origem = payload.get("phone", "")
    if payload.get("isGroup") and not origem.endswith("@g.us"):
        origem = origem.removesuffix("-group") + "@g.us"
    return [{
        "direcao": "saida" if de_mim else "entrada",
        "origem": origem,
        "autor": "equipe" if de_mim else payload.get("senderName"),
        "texto": texto,
        "ocorrido_em": _ts(payload.get("momment")),
        "external_id": f"wa:{payload.get('messageId')}",
    }]


def parse(payload: dict[str, Any]) -> list[dict]:
    """Detecta o formato pelo conteúdo do JSON."""
    if payload.get("object") == "whatsapp_business_account":
        return parse_meta(payload)
    if "event" in payload and "data" in payload:
        return parse_evolution(payload)
    return parse_zapi(payload)
