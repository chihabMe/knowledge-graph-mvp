from django.db import migrations
from django.utils import timezone

# Must match integrations.freshness.FRESHNESS_HEARTBEAT_NAME (migrations
# cannot import application modules).
FRESHNESS_HEARTBEAT_NAME = "freshness-monitor"


def seed_freshness_heartbeat(apps, schema_editor):
    """Seed the heartbeat at migrate time so a fresh deploy starts green.

    Deploying is the moment beat is about to start; without a row the health
    endpoint reports error until the first monitor tick. A beat that never
    starts still alerts once the seeded tick exceeds the heartbeat max age.
    """
    heartbeat = apps.get_model("integrations", "SchedulerHeartbeat")
    heartbeat.objects.get_or_create(
        name=FRESHNESS_HEARTBEAT_NAME,
        defaults={"last_tick_at": timezone.now()},
    )


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0010_scheduler_heartbeat"),
    ]

    operations = [
        migrations.RunPython(seed_freshness_heartbeat, migrations.RunPython.noop),
    ]
