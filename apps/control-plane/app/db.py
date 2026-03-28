from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    connect_args: dict[str, object] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(
        database_url,
        connect_args=connect_args,
        future=True,
        pool_pre_ping=True,
    )
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db(session_factory: sessionmaker[Session]) -> None:
    from .models import Job, JobArtifact

    _ = (Job, JobArtifact)
    engine = session_factory.kw["bind"]
    Base.metadata.create_all(bind=engine)
