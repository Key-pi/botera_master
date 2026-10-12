# Generated manually for the Foundation milestone.

import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="PlatformDiagnostic",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("retain_until", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("code", models.CharField(db_index=True, max_length=128)),
                ("severity", models.CharField(choices=[("info", "Info"), ("warning", "Warning"), ("critical", "Critical")], max_length=16)),
                ("message", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="platformdiagnostic",
            index=models.Index(fields=["severity", "created_at"], name="audit_platf_severit_4e2f01_idx"),
        ),
        migrations.AddIndex(
            model_name="platformdiagnostic",
            index=models.Index(fields=["code", "created_at"], name="audit_platf_code_19f83b_idx"),
        ),
    ]
