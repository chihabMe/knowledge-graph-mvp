"""Identity-free freshness aggregation over synchronization bookkeeping.

The report is operational evidence only; it never grants access. Every field
is a status label, count, duration, or worst-case age. Identities, Drive IDs,
document titles, provider payloads, and secrets never leave this module.
"""

import datetime
from dataclasses import asdict, dataclass

from django.conf import settings
from django.utils import timezone

from integrations.models import (
    DriveConnection,
    GoogleDriveAuthorization,
    PermissionSyncRun,
    SchedulerHeartbeat,
    SourceDocument,
    UserDocumentVisibility,
    UserVisibilitySyncRun,
)

FRESHNESS_HEARTBEAT_NAME = "freshness-monitor"

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_ERROR = "error"


@dataclass(frozen=True)
class FreshnessReport:
    """Aggregated state safe for health responses and Celery results."""

    status: str
    permission_authority: str
    heartbeat_age_seconds: int | None
    active_connections: int
    active_authorizations: int
    sync_targets: int
    targets_never_succeeded: int
    targets_expiring_soon: int
    targets_expired: int
    worst_last_success_age_seconds: int | None
    worst_remaining_evidence_seconds: int | None
    queued_runs: int
    oldest_queued_run_age_seconds: int | None
    running_runs: int
    longest_running_run_age_seconds: int | None
    worst_recent_run_duration_seconds: int | None
    max_consecutive_failures: int
    latest_denied_documents: int
    unknown_documents: int
    latest_error_runs: int
    expired_evidence_documents: int

    def as_payload(self) -> dict:
        return asdict(self)


@dataclass
class _Aggregation:
    sync_targets: int = 0
    targets_never_succeeded: int = 0
    targets_expiring_soon: int = 0
    targets_expired: int = 0
    worst_last_success_age_seconds: int | None = None
    worst_remaining_evidence_seconds: int | None = None
    queued_runs: int = 0
    oldest_queued_run_age_seconds: int | None = None
    running_runs: int = 0
    longest_running_run_age_seconds: int | None = None
    worst_recent_run_duration_seconds: int | None = None
    max_consecutive_failures: int = 0
    latest_denied_documents: int = 0
    unknown_documents: int = 0
    latest_error_runs: int = 0
    expired_evidence_documents: int = 0


def _age_seconds(now: datetime.datetime, value: datetime.datetime | None) -> int | None:
    if value is None:
        return None
    return max(0, int((now - value).total_seconds()))


def _maximum(current: int | None, candidate: int | None) -> int | None:
    if candidate is None:
        return current
    return candidate if current is None else max(current, candidate)


def _minimum(current: int | None, candidate: int | None) -> int | None:
    if candidate is None:
        return current
    return candidate if current is None else min(current, candidate)


def _record_target_freshness(
    aggregate: _Aggregation,
    *,
    now: datetime.datetime,
    last_success: datetime.datetime | None,
    max_age_seconds: int,
    warn_fraction: float,
) -> None:
    aggregate.sync_targets += 1
    age = _age_seconds(now, last_success)
    if age is None:
        aggregate.targets_never_succeeded += 1
        aggregate.targets_expired += 1
        remaining = 0
    else:
        aggregate.worst_last_success_age_seconds = _maximum(
            aggregate.worst_last_success_age_seconds,
            age,
        )
        remaining = max(0, max_age_seconds - age)
        if remaining <= 0:
            aggregate.targets_expired += 1
        elif remaining < warn_fraction * max_age_seconds:
            aggregate.targets_expiring_soon += 1
    aggregate.worst_remaining_evidence_seconds = _minimum(
        aggregate.worst_remaining_evidence_seconds,
        remaining,
    )


def _record_run_ages(aggregate: _Aggregation, *, now: datetime.datetime, runs) -> None:
    for run in runs:
        if run.status == run.Status.QUEUED:
            aggregate.queued_runs += 1
            aggregate.oldest_queued_run_age_seconds = _maximum(
                aggregate.oldest_queued_run_age_seconds,
                _age_seconds(now, run.created_at),
            )
        elif run.status == run.Status.RUNNING:
            aggregate.running_runs += 1
            aggregate.longest_running_run_age_seconds = _maximum(
                aggregate.longest_running_run_age_seconds,
                _age_seconds(now, run.started_at or run.created_at),
            )


def _record_latest_run(aggregate: _Aggregation, runs) -> None:
    completed_runs = [
        run for run in runs if run.status not in {run.Status.QUEUED, run.Status.RUNNING}
    ]
    if not completed_runs:
        return
    latest = completed_runs[0]
    aggregate.latest_error_runs += int(latest.status == latest.Status.FAILED)
    aggregate.latest_denied_documents += getattr(latest, "documents_denied", 0)
    aggregate.unknown_documents += getattr(latest, "documents_excluded", 0)
    if latest.started_at and latest.finished_at:
        duration = max(0, int((latest.finished_at - latest.started_at).total_seconds()))
        aggregate.worst_recent_run_duration_seconds = _maximum(
            aggregate.worst_recent_run_duration_seconds,
            duration,
        )
    consecutive_failures = 0
    for run in completed_runs:
        if run.status != run.Status.FAILED:
            break
        consecutive_failures += 1
    aggregate.max_consecutive_failures = max(
        aggregate.max_consecutive_failures,
        consecutive_failures,
    )


def _record_evidence_remaining(
    aggregate: _Aggregation,
    *,
    now: datetime.datetime,
    verified_at: datetime.datetime | None,
    max_age_seconds: int,
) -> None:
    age = _age_seconds(now, verified_at)
    remaining = 0 if age is None else max(0, max_age_seconds - age)
    aggregate.worst_remaining_evidence_seconds = _minimum(
        aggregate.worst_remaining_evidence_seconds,
        remaining,
    )
    if remaining <= 0:
        aggregate.expired_evidence_documents += 1


def _active_connections(authority: str) -> list[DriveConnection]:
    return [
        connection
        for connection in DriveConnection.objects.filter(
            enabled=True,
            permission_authority=authority,
        ).order_by("pk")
        if connection.effective_root_id
    ]


def _aggregate_per_user(
    aggregate: _Aggregation,
    *,
    connections: list[DriveConnection],
    now: datetime.datetime,
) -> int:
    authorizations = list(
        GoogleDriveAuthorization.objects.filter(
            connection__in=connections,
            status=GoogleDriveAuthorization.Status.ACTIVE,
        )
        .select_related("connection")
        .order_by("pk")
    )
    authorizations = [
        authorization
        for authorization in authorizations
        if authorization.connection_generation == authorization.connection.authorization_generation
    ]
    for authorization in authorizations:
        _record_target_freshness(
            aggregate,
            now=now,
            last_success=authorization.last_successful_visibility_sync_at,
            max_age_seconds=settings.GOOGLE_USER_VISIBILITY_MAX_AGE_SECONDS,
            warn_fraction=settings.FRESHNESS_WARN_REMAINING_FRACTION,
        )
        runs = list(
            UserVisibilitySyncRun.objects.filter(
                authorization=authorization,
                connection_generation=authorization.connection_generation,
                authorization_generation=authorization.authorization_generation,
            ).order_by("-pk")
        )
        _record_run_ages(aggregate, now=now, runs=runs)
        _record_latest_run(aggregate, runs)
        current_evidence = UserDocumentVisibility.objects.filter(
            authorization=authorization,
            connection_generation=authorization.connection_generation,
            authorization_generation=authorization.authorization_generation,
        )
        aggregate.unknown_documents += current_evidence.filter(
            state=UserDocumentVisibility.State.UNKNOWN,
        ).count()
        for verified_at in current_evidence.filter(
            state=UserDocumentVisibility.State.VERIFIED_VISIBLE,
        ).values_list("spicedb_verified_at", flat=True):
            _record_evidence_remaining(
                aggregate,
                now=now,
                verified_at=verified_at,
                max_age_seconds=settings.GOOGLE_USER_VISIBILITY_MAX_AGE_SECONDS,
            )
    return len(authorizations)


def _aggregate_delegated(
    aggregate: _Aggregation,
    *,
    connections: list[DriveConnection],
    now: datetime.datetime,
) -> None:
    for connection in connections:
        runs = list(PermissionSyncRun.objects.filter(connection=connection).order_by("-pk"))
        last_success = next(
            (
                run.finished_at
                for run in runs
                if run.status in {run.Status.SUCCEEDED, run.Status.PARTIAL}
                and run.finished_at is not None
            ),
            None,
        )
        _record_target_freshness(
            aggregate,
            now=now,
            last_success=last_success,
            max_age_seconds=settings.PERMISSION_VERIFICATION_MAX_AGE_SECONDS,
            warn_fraction=settings.FRESHNESS_WARN_REMAINING_FRACTION,
        )
        _record_run_ages(aggregate, now=now, runs=runs)
        _record_latest_run(aggregate, runs)
        documents = SourceDocument.objects.filter(
            connection=connection,
            active_in_scope=True,
            retrieval_eligible=True,
        )
        for verified_at in documents.values_list("spicedb_verified_at", flat=True):
            _record_evidence_remaining(
                aggregate,
                now=now,
                verified_at=verified_at,
                max_age_seconds=settings.PERMISSION_VERIFICATION_MAX_AGE_SECONDS,
            )


def build_freshness_report(*, now: datetime.datetime | None = None) -> FreshnessReport:
    now = now or timezone.now()
    heartbeat = SchedulerHeartbeat.objects.filter(name=FRESHNESS_HEARTBEAT_NAME).first()
    heartbeat_age = _age_seconds(now, heartbeat.last_tick_at if heartbeat else None)
    authority = settings.GOOGLE_PERMISSION_AUTHORITY
    connections = _active_connections(authority)
    aggregate = _Aggregation()
    active_authorizations = 0
    if authority == DriveConnection.PermissionAuthority.PER_USER_OAUTH:
        active_authorizations = _aggregate_per_user(
            aggregate,
            connections=connections,
            now=now,
        )
    elif authority == DriveConnection.PermissionAuthority.DELEGATED_ACL:
        _aggregate_delegated(aggregate, connections=connections, now=now)

    heartbeat_stale = (
        heartbeat_age is None or heartbeat_age > settings.FRESHNESS_HEARTBEAT_MAX_AGE_SECONDS
    )
    if heartbeat_stale or aggregate.targets_expired or aggregate.expired_evidence_documents:
        status = STATUS_ERROR
    elif (
        aggregate.targets_expiring_soon
        or aggregate.unknown_documents
        or aggregate.max_consecutive_failures
        or aggregate.latest_error_runs
    ):
        status = STATUS_WARN
    else:
        status = STATUS_OK

    return FreshnessReport(
        status=status,
        permission_authority=authority,
        heartbeat_age_seconds=heartbeat_age,
        active_connections=len(connections),
        active_authorizations=active_authorizations,
        **asdict(aggregate),
    )
