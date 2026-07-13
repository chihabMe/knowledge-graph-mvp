from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from integrations.models import SourceDocument, SourceDocumentContent
from integrations.tasks import queue_document_extraction


class Command(BaseCommand):
    help = (
        "Queue current stored documents for idempotent graph re-extraction so "
        "their Chunk nodes receive embeddings from the configured provider."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-document-id",
            action="append",
            dest="source_document_ids",
            type=int,
            help="Queue one SourceDocument id; repeat the option for multiple documents.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Queue every document whose stored content matches its current content hash.",
        )

    def handle(self, *args, **options):
        source_document_ids = tuple(options["source_document_ids"] or ())
        queue_all = options["all"]
        if queue_all == bool(source_document_ids):
            raise CommandError("Choose exactly one of --all or --source-document-id.")

        documents = SourceDocument.objects.select_related("content").order_by("pk")
        if source_document_ids:
            documents = documents.filter(pk__in=source_document_ids)

        queued = 0
        skipped = 0
        now = timezone.now()
        for document in documents:
            try:
                stored = document.content
            except SourceDocumentContent.DoesNotExist:
                skipped += 1
                continue
            if (
                not document.content_hash
                or stored.content_hash != document.content_hash
                or document.graph_extraction_status == SourceDocument.GraphExtractionStatus.RUNNING
            ):
                skipped += 1
                continue

            claimed = (
                SourceDocument.objects.filter(
                    pk=document.pk,
                    content_hash=document.content_hash,
                )
                .exclude(graph_extraction_status=SourceDocument.GraphExtractionStatus.RUNNING)
                .update(
                    graph_extraction_status=SourceDocument.GraphExtractionStatus.PENDING,
                    graph_extraction_error_summary="",
                    graph_extraction_attempts=0,
                    graph_extraction_queued_at=now,
                    graph_extraction_started_at=None,
                    graph_extraction_finished_at=None,
                    updated_at=now,
                )
            )
            if not claimed:
                skipped += 1
                continue
            queue_document_extraction.delay(document.pk, document.content_hash)
            queued += 1

        self.stdout.write(self.style.SUCCESS(f"Queued {queued}; skipped {skipped}."))
