from collections.abc import Callable

from django.conf import settings
from django.db import connections
from neo4j import GraphDatabase
from redis import Redis

from authorization.client import AuthzedSpiceDB

ServiceCheck = Callable[[], None]


def check_django() -> None:
    return None


def check_postgres() -> None:
    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def check_redis() -> None:
    client = Redis.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    client.ping()


def check_neo4j() -> None:
    driver = GraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        connection_timeout=1,
    )
    try:
        driver.verify_connectivity()
    finally:
        driver.close()


def check_spicedb() -> None:
    AuthzedSpiceDB().check()


SERVICE_CHECKS: dict[str, ServiceCheck] = {
    "django": check_django,
    "postgres": check_postgres,
    "redis": check_redis,
    "neo4j": check_neo4j,
    "spicedb": check_spicedb,
}


def collect_health() -> tuple[str, dict[str, str]]:
    service_statuses = {}

    for service_name, check in SERVICE_CHECKS.items():
        try:
            check()
        except Exception:
            service_statuses[service_name] = "error"
        else:
            service_statuses[service_name] = "ok"

    overall_status = "ok"
    if any(status != "ok" for status in service_statuses.values()):
        overall_status = "degraded"

    return overall_status, service_statuses
