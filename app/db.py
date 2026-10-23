from datetime import UTC, datetime

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_url = get_settings().database_url
engine = create_engine(
    _url, connect_args={"check_same_thread": False} if _url.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    from app import models  # noqa: F401  registra as tabelas

    Base.metadata.create_all(engine)
    _adicionar_colunas_novas()


def _adicionar_colunas_novas() -> None:
    """create_all não altera tabelas existentes; acrescenta as colunas que faltam."""
    existentes = inspect(engine)
    with engine.begin() as conn:
        for tabela in Base.metadata.sorted_tables:
            if not existentes.has_table(tabela.name):
                continue
            colunas = {c["name"] for c in existentes.get_columns(tabela.name)}
            for coluna in tabela.columns:
                if coluna.name not in colunas:
                    tipo = coluna.type.compile(engine.dialect)
                    conn.execute(text(f'ALTER TABLE {tabela.name} ADD COLUMN {coluna.name} {tipo}'))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def agora_utc() -> datetime:
    """Datetime UTC sem fuso: o banco guarda tudo em UTC."""
    return datetime.now(UTC).replace(tzinfo=None)
