# db.py
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine():
    return create_engine(get_settings().database_url, pool_pre_ping=True, future=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


def get_session() -> Session:
    return get_sessionmaker()()


def init_db() -> None:
    """Create tables if they don't exist. Import models first so they're registered."""
    from models import File  # noqa: F401 — side-effect import to register mapping

    Base.metadata.create_all(bind=get_engine())
