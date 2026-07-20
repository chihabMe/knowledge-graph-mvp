"""Identity-free freshness aggregation over synchronization bookkeeping.

The report is operational evidence only; it never grants access. Every field
is a status label, count, duration, or worst-case age. Identities, Drive IDs,
document titles, provider payloads, and secrets never leave this module.

Aggregation runs on every monitor tick, so per-row work must stay bounded:
backlog and evidence statistics are database aggregates, and only a small
recent sample of completed runs is ever loaded.
"""

import datetime
from dataclasses import asdict, dataclass

from django.conf import settings
from django.db.models import Count, Min, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from integrations.models import (
    DriveConnection,
    DriveSyncRun,
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
    content_sync_targets: int
    content_sync_never_succeeded: int
    content_sync_expiring_soon: int
    content_sync_overdue: int
    worst_content_sync_age_seconds: int | None
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
    content_refresh_pending_documents: int
    content_extraction_failed_documents: int

    def as_payload(self) -> dict:
        return asdict(self)


@dataclass
class _Aggregation:
    content_sync_targets: int = 0
    content_sync_never_succeeded: int = 0
    content_sync_expiring_soon: int = 0
    content_sync_overdue: int = 0
    worst_content_sync_age_seconds: int | None = None
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
    content_refresh_pending_documents: int = 0
    content_extraction_failed_documents: int = 0


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
    first_seen_at: datetime.datetime | None,
    max_age_seconds: int,
    warn_fraction: float,
    grace_seconds: int,
) -> None:
    aggregate.sync_targets += 1
    age = _age_seconds(now, last_success)
    if age is None:
        aggregate.targets_never_succeeded += 1
        # Retrieval denies a never-synced target either way; the grace window
        # only keeps a just-connected target at warn instead of paging it as
        # an outage. An unknown first-seen time gets no grace.
        first_seen_age = _age_seconds(now, first_seen_at)
        if first_seen_age is not None and first_seen_age <= grace_seconds:
            aggregate.targets_expiring_soon += 1
        else:
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


def _record_run_backlog(aggregate: _Aggregation, *, now: datetime.datetime, runs) -> None:
    """Backlog counts and worst ages via DB aggregates; loads no run rows."""
    model = runs.model
    stats = runs.aggregate(
        queued=Count("pk", filter=Q(status=model.Status.QUEUED)),
        oldest_queued_at=Min("created_at", filter=Q(status=model.Status.QUEUED)),
        running=Count("pk", filter=Q(status=model.Status.RUNNING)),
        oldest_running_at=Min(
            Coalesce("started_at", "created_at"),
            filter=Q(status=model.Status.RUNNING),
        ),
    )
    aggregate.queued_runs += stats["queued"]
    aggregate.oldest_queued_run_age_seconds = _maximum(
        aggregate.oldest_queued_run_age_seconds,
        _age_seconds(now, stats["oldest_queued_at"]),
    )
    aggregate.running_runs += stats["running"]
    aggregate.longest_running_run_age_seconds = _maximum(
        aggregate.longest_running_run_age_seconds,
        _age_seconds(now, stats["oldest_running_at"]),
    )


def _record_recent_runs(aggregate: _Aggregation, runs, *, sample_limit: int) -> None:
    """Latest-result and failure-streak stats over a bounded recent sample.

    The streak reading caps at sample_limit; a streak at or beyond the cap
    already drives a warning, which is all the status logic needs.
    """
    model = runs.model
    completed_runs = list(
        runs.exclude(status__in=[model.Status.QUEUED, model.Status.RUNNING]).order_by("-pk")[
            :sample_limit
        ]
    )
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


def _record_evidence_expiry(
    aggregate: _Aggregation,
    *,
    now: datetime.datetime,
    evidence,
    max_age_seconds: int,
) -> None:
    """Expired count and worst remaining lifetime via DB aggregates.

    Works over any queryset carrying spicedb_verified_at; a null timestamp
    counts as already expired (fail closed). Loads no evidence rows.
    """
    cutoff = now - datetime.timedelta(seconds=max_age_seconds)
    stats = evidence.aggregate(
        total=Count("pk"),
        expired=Count(
            "pk",
            filter=Q(spicedb_verified_at=None) | Q(spicedb_verified_at__lte=cutoff),
        ),
        oldest_verified_at=Min("spicedb_verified_at"),
    )
    aggregate.expired_evidence_documents += stats["expired"]
    if not stats["total"]:
        return
    if stats["expired"]:
        remaining = 0
    else:
        age = _age_seconds(now, stats["oldest_verified_at"])
        remaining = 0 if age is None else max(0, max_age_seconds - age)
    aggregate.worst_remaining_evidence_seconds = _minimum(
        aggregate.worst_remaining_evidence_seconds,
        remaining,
    )


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
            first_seen_at=authorization.created_at,
            max_age_seconds=settings.GOOGLE_USER_VISIBILITY_MAX_AGE_SECONDS,
            warn_fraction=settings.FRESHNESS_WARN_REMAINING_FRACTION,
            grace_seconds=settings.FRESHNESS_NEVER_SYNCED_GRACE_SECONDS,
        )
        runs = UserVisibilitySyncRun.objects.filter(
            authorization=authorization,
            connection_generation=authorization.connection_generation,
            authorization_generation=authorization.authorization_generation,
        )
        _record_run_backlog(aggregate, now=now, runs=runs)
        _record_recent_runs(aggregate, runs, sample_limit=settings.FRESHNESS_RUN_SAMPLE_LIMIT)
        current_evidence = UserDocumentVisibility.objects.filter(
            authorization=authorization,
            connection_generation=authorization.connection_generation,
            authorization_generation=authorization.authorization_generation,
        )
        aggregate.unknown_documents += current_evidence.filter(
            state=UserDocumentVisibility.State.UNKNOWN,
        ).count()
        _record_evidence_expiry(
            aggregate,
            now=now,
            evidence=current_evidence.filter(
                state=UserDocumentVisibility.State.VERIFIED_VISIBLE,
            ),
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
        runs = PermissionSyncRun.objects.filter(connection=connection)
        last_success = (
            runs.filter(
                status__in=[
                    PermissionSyncRun.Status.SUCCEEDED,
                    PermissionSyncRun.Status.PARTIAL,
                ],
                finished_at__isnull=False,
            )
            .order_by("-pk")
            .values_list("finished_at", flat=True)
            .first()
        )
        _record_target_freshness(
            aggregate,
            now=now,
            last_success=last_success,
            first_seen_at=connection.created_at,
            max_age_seconds=settings.PERMISSION_VERIFICATION_MAX_AGE_SECONDS,
            warn_fraction=settings.FRESHNESS_WARN_REMAINING_FRACTION,
            grace_seconds=settings.FRESHNESS_NEVER_SYNCED_GRACE_SECONDS,
        )
        _record_run_backlog(aggregate, now=now, runs=runs)
        _record_recent_runs(aggregate, runs, sample_limit=settings.FRESHNESS_RUN_SAMPLE_LIMIT)
        _record_evidence_expiry(
            aggregate,
            now=now,
            evidence=SourceDocument.objects.filter(
                connection=connection,
                active_in_scope=True,
                retrieval_eligible=True,
            ),
            max_age_seconds=settings.PERMISSION_VERIFICATION_MAX_AGE_SECONDS,
        )


def _record_content_currency(
    aggregate: _Aggregation,
    *,
    connections: list[DriveConnection],
) -> None:
    """Count retrieval-eligible documents whose graph content lags PostgreSQL.

    While extraction is pending or failed the retrieval content-currency gate
    refuses those documents, so the counts explain refusal windows: pending
    is informational, failed drives a warning.
    """
    documents = SourceDocument.objects.filter(
        connection__in=connections,
        active_in_scope=True,
        retrieval_eligible=True,
    )
    aggregate.content_refresh_pending_documents = documents.filter(
        graph_extraction_status__in=[
            SourceDocument.GraphExtractionStatus.PENDING,
            SourceDocument.GraphExtractionStatus.RUNNING,
        ],
    ).count()
    aggregate.content_extraction_failed_documents = documents.filter(
        graph_extraction_status=SourceDocument.GraphExtractionStatus.FAILED,
    ).count()


def _record_content_sync_freshness(
    aggregate: _Aggregation,
    *,
    connections: list[DriveConnection],
    now: datetime.datetime,
) -> None:
    """Aggregate periodic Drive content-sync age without exposing identities."""
    max_age = settings.DRIVE_CONTENT_SYNC_MAX_AGE_SECONDS
    warning_age = max_age * (1.0 - settings.FRESHNESS_WARN_REMAINING_FRACTION)
    for connection in connections:
        aggregate.content_sync_targets += 1
        runs = DriveSyncRun.objects.filter(connection=connection)
        last_success = (
            runs.filter(
                status=DriveSyncRun.Status.SUCCEEDED,
                finished_at__isnull=False,
            )
            .order_by("-pk")
            .values_list("finished_at", flat=True)
            .first()
        )
        age = _age_seconds(now, last_success)
        if age is None:
            aggregate.content_sync_never_succeeded += 1
            first_seen_age = _age_seconds(now, connection.created_at)
            if (
                first_seen_age is not None
                and first_seen_age <= settings.FRESHNESS_NEVER_SYNCED_GRACE_SECONDS
            ):
                aggregate.content_sync_expiring_soon += 1
            else:
                aggregate.content_sync_overdue += 1
        else:
            aggregate.worst_content_sync_age_seconds = _maximum(
                aggregate.worst_content_sync_age_seconds,
                age,
            )
            if age >= max_age:
                aggregate.content_sync_overdue += 1
            elif age > warning_age:
                aggregate.content_sync_expiring_soon += 1
        _record_run_backlog(aggregate, now=now, runs=runs)
        _record_recent_runs(aggregate, runs, sample_limit=settings.FRESHNESS_RUN_SAMPLE_LIMIT)


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
    _record_content_sync_freshness(aggregate, connections=connections, now=now)
    _record_content_currency(aggregate, connections=connections)

    heartbeat_stale = (
        heartbeat_age is None or heartbeat_age > settings.FRESHNESS_HEARTBEAT_MAX_AGE_SECONDS
    )
    if (
        heartbeat_stale
        or aggregate.targets_expired
        or aggregate.expired_evidence_documents
        or aggregate.content_sync_overdue
    ):
        status = STATUS_ERROR
    elif (
        aggregate.targets_expiring_soon
        or aggregate.content_sync_expiring_soon
        or aggregate.unknown_documents
        or aggregate.max_consecutive_failures
        or aggregate.latest_error_runs
        or aggregate.content_extraction_failed_documents
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
