import threading
from collections.abc import Generator
from contextlib import contextmanager

from django.conf import settings
from neo4j import Driver, GraphDatabase, Session, Transaction

_driver: Driver | None = None
_driver_lock = threading.Lock()


def get_driver() -> Driver:
    # One driver per process, reused across sessions (it owns the connection
    # pool) — matches how the neo4j package expects to be used, unlike the
    # health check's driver, which is short-lived on purpose for a liveness
    # probe rather than a shared worker/request path.
    #
    # Creation must stay lazy: under Celery prefork a driver created in the
    # parent before fork would share sockets across children. Never call
    # get_driver() at import time.
    global _driver
    if _driver is None:
        with _driver_lock:
            if _driver is None:
                _driver = GraphDatabase.driver(
                    settings.NEO4J_URI,
                    auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
                )
    return _driver


def close_driver() -> None:
    global _driver
    with _driver_lock:
        if _driver is not None:
            _driver.close()
            _driver = None


@contextmanager
def session() -> Generator[Session, None, None]:
    with get_driver().session() as db_session:
        yield db_session


@contextmanager
def write_transaction() -> Generator[Transaction, None, None]:
    """Yield one explicit transaction for an all-or-nothing graph write."""
    with get_driver().session() as db_session:
        with db_session.begin_transaction() as transaction:
            yield transaction
