from django.db import migrations


TEMPLATES = {
    "uk": "Новий підтверджений запис: {client_name} {client_phone}\n{date} о {time}\n{services}{comment}",
    "ru": "Новая подтверждённая запись: {client_name} {client_phone}\n{date} в {time}\n{services}{comment}",
    "en": "New confirmed booking: {client_name} {client_phone}\n{date} at {time}\n{services}{comment}",
}
PLACEHOLDERS = [
    "client_name",
    "client_phone",
    "date",
    "time",
    "services",
    "comment",
]


def add_master_client_booked_template(apps, schema_editor):
    SystemTemplate = apps.get_model("bot", "SystemTemplate")

    for language, body in TEMPLATES.items():
        SystemTemplate.objects.update_or_create(
            key="master.client_booked_appointment",
            language=language,
            defaults={
                "body": body,
                "content_type": "message",
                "editable": True,
                "is_required": True,
                "placeholder_allowlist": PLACEHOLDERS,
                "sample_context": {placeholder: f"<{placeholder}>" for placeholder in PLACEHOLDERS},
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("bot", "0007_add_failed_reminder_action_templates"),
    ]

    operations = [
        migrations.RunPython(
            add_master_client_booked_template,
            migrations.RunPython.noop,
        ),
    ]
