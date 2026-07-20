from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0011_seed_freshness_heartbeat"),
    ]

    operations = [
        migrations.AlterField(
            model_name="driveconnection",
            name="permission_authority",
            field=models.CharField(
                choices=[
                    ("delegated_acl", "Delegated ACL (legacy)"),
                    ("per_user_oauth", "Per-user OAuth"),
                ],
                default="per_user_oauth",
                max_length=32,
            ),
        ),
    ]
