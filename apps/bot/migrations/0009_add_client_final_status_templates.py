from django.db import migrations


TEMPLATES = {
    "appointment.completed": {
        "placeholders": ["partner_name", "date", "time", "services"],
        "translations": {
            "uk": "Візит до {partner_name} завершено: {date} о {time}, {services}.",
            "ru": "Визит к {partner_name} завершён: {date} в {time}, {services}.",
            "en": "Visit with {partner_name} completed: {date} at {time}, {services}.",
        },
    },
    "appointment.no_show": {
        "placeholders": ["partner_name", "date", "time", "services"],
        "translations": {
            "uk": "{partner_name} позначив запис як неявку: {date} о {time}, {services}.",
            "ru": "{partner_name} отметил запись как неявку: {date} в {time}, {services}.",
            "en": "{partner_name} marked the booking as a no-show: {date} at {time}, {services}.",
        },
    },
}


def add_client_final_status_templates(apps, schema_editor):
    SystemTemplate = apps.get_model("bot", "SystemTemplate")

    for key, definition in TEMPLATES.items():
        placeholders = definition["placeholders"]
        for language, body in definition["translations"].items():
            SystemTemplate.objects.update_or_create(
                key=key,
                language=language,
                defaults={
                    "body": body,
                    "content_type": "message",
                    "editable": True,
                    "is_required": True,
                    "placeholder_allowlist": placeholders,
                    "sample_context": {
                        placeholder: f"<{placeholder}>" for placeholder in placeholders
                    },
                },
            )


class Migration(migrations.Migration):
    dependencies = [
        ("bot", "0008_add_master_client_booked_template"),
    ]

    operations = [
        migrations.RunPython(
            add_client_final_status_templates,
            migrations.RunPython.noop,
        ),
    ]
