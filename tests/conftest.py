import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["PAINEL_TOKEN"] = ""
os.environ["SYNC_AUTOMATICO_MINUTOS"] = "0"
os.environ["SLACK_SIGNING_SECRET"] = ""
os.environ["WHATSAPP_WEBHOOK_SECRET"] = ""
os.environ["WHATSAPP_APP_SECRET"] = ""

import pytest  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.db as dbmod  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    dbmod.engine = engine
    dbmod.SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    from app import models  # noqa: F401

    dbmod.Base.metadata.create_all(engine)
    s = dbmod.SessionLocal()
    yield s
    s.close()
