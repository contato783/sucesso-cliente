from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./sucesso_cliente.db"
    painel_token: str = ""

    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "low"

    whatsapp_verify_token: str = ""
    whatsapp_app_secret: str = ""
    whatsapp_webhook_secret: str = ""

    slack_bot_token: str = ""
    slack_signing_secret: str = ""

    sponte_base_url: str = "https://api.sponteeducacional.net.br/WSAPIEdu.asmx"
    sponte_codigo_cliente: str = ""
    sponte_token: str = ""
    sponte_metodo_alunos: str = "GetAlunos"
    sponte_metodo_parcelas: str = "GetParcelas"

    peso_engajamento: float = 30
    peso_sentimento: float = 25
    peso_financeiro: float = 25
    peso_resultado: float = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
