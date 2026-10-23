# Generated manually for the Foundation milestone.

import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="IntegrationEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("retain_until", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("event_id", models.UUIDField(db_index=True, unique=True)),
                ("event_type", models.CharField(db_index=True, max_length=128)),
                ("source_product", models.CharField(default="botera_master", max_length=64)),
                ("source_business_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("sending", "Sending"), ("sent", "Sent"), ("retry", "Retry"), ("failed", "Failed")], default="pending", max_length=16)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("last_error_code", models.CharField(blank=True, max_length=128)),
                ("last_error_message_masked", models.TextField(blank=True)),
                ("next_retry_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="integrationevent",
            index=models.Index(fields=["status", "next_retry_at"], name="integration_status_533016_idx"),
        ),
        migrations.AddIndex(
            model_name="integrationevent",
            index=models.Index(fields=["source_product", "event_type"], name="integration_source__9a5a49_idx"),
        ),
    ]
