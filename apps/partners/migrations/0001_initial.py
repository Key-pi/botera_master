# Generated manually for the Foundation milestone.

import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Partner",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("status", models.CharField(choices=[("setup_required", "Setup required"), ("active", "Active"), ("suspended", "Suspended"), ("archived", "Archived")], default="setup_required", max_length=32)),
                ("timezone", models.CharField(default="Europe/Kiev", max_length=64)),
                ("default_language", models.CharField(default="uk", max_length=8)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
    ]
