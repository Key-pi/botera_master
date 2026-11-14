from django.db import migrations


TEMPLATE_KEY = "appointment.schedule_conflict_reschedule_requested"
PLACEHOLDERS = ["partner_name", "date", "time", "services"]
TRANSLATIONS = {
    "uk": (
        "У графіку {partner_name} відбулися зміни. Будь ласка, оберіть інший час "
        "для запису {date} о {time}: {services}. Поточний запис залишається "
        "підтвердженим, доки ви не оберете новий час."
    ),
    "ru": (
        "В графике {partner_name} произошли изменения. Пожалуйста, выберите другое "
        "время для записи {date} в {time}: {services}. Текущая запись остаётся "
        "подтверждённой, пока вы не выберете новое время."
    ),
    "en": (
        "The schedule at {partner_name} has changed. Please choose another time for "
        "your booking on {date} at {time}: {services}. Your current booking stays "
        "confirmed until you choose a new time."
    ),
}


def add_schedule_conflict_reschedule_template(apps, schema_editor):
    SystemTemplate = apps.get_model("bot", "SystemTemplate")

    for language, body in TRANSLATIONS.items():
        SystemTemplate.objects.update_or_create(
            key=TEMPLATE_KEY,
            language=language,
            defaults={
                "body": body,
                "content_type": "message",
                "editable": True,
                "is_required": True,
                "placeholder_allowlist": PLACEHOLDERS,
                "sample_context": {
                    placeholder: f"<{placeholder}>" for placeholder in PLACEHOLDERS
                },
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("bot", "0010_add_client_penalty_templates"),
    ]

    operations = [
        migrations.RunPython(
            add_schedule_conflict_reschedule_template,
            migrations.RunPython.noop,
        ),
    ]
