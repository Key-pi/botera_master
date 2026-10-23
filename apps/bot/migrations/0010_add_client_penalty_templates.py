from django.db import migrations


TEMPLATES = {
    "penalty.created_late_cancel": {
        "placeholders": [
            "penalty_amount",
            "currency",
            "appointment_date",
            "appointment_time",
        ],
        "translations": {
            "uk": "Створено штраф за пізнє скасування: {penalty_amount} {currency}, запис {appointment_date} о {appointment_time}.",
            "ru": "Создан штраф за позднюю отмену: {penalty_amount} {currency}, запись {appointment_date} в {appointment_time}.",
            "en": "Late-cancel penalty created: {penalty_amount} {currency}, booking {appointment_date} at {appointment_time}.",
        },
    },
    "penalty.created_no_show": {
        "placeholders": [
            "penalty_amount",
            "currency",
            "appointment_date",
            "appointment_time",
        ],
        "translations": {
            "uk": "Створено штраф за no-show: {penalty_amount} {currency}, запис {appointment_date} о {appointment_time}.",
            "ru": "Создан штраф за no-show: {penalty_amount} {currency}, запись {appointment_date} в {appointment_time}.",
            "en": "No-show penalty created: {penalty_amount} {currency}, booking {appointment_date} at {appointment_time}.",
        },
    },
    "penalty.created_late_arrival": {
        "placeholders": [
            "penalty_amount",
            "currency",
            "late_minutes",
            "appointment_date",
            "appointment_time",
        ],
        "translations": {
            "uk": "Створено штраф за запізнення на {late_minutes} хв: {penalty_amount} {currency}, запис {appointment_date} о {appointment_time}.",
            "ru": "Создан штраф за опоздание на {late_minutes} мин: {penalty_amount} {currency}, запись {appointment_date} в {appointment_time}.",
            "en": "Late-arrival penalty created for {late_minutes} min: {penalty_amount} {currency}, booking {appointment_date} at {appointment_time}.",
        },
    },
    "penalty.paid": {
        "placeholders": ["penalty_amount", "currency"],
        "translations": {
            "uk": "Штраф оплачено: {penalty_amount} {currency}.",
            "ru": "Штраф оплачен: {penalty_amount} {currency}.",
            "en": "Penalty paid: {penalty_amount} {currency}.",
        },
    },
    "penalty.waived": {
        "placeholders": ["penalty_amount", "currency"],
        "translations": {
            "uk": "Штраф списано: {penalty_amount} {currency}.",
            "ru": "Штраф списан: {penalty_amount} {currency}.",
            "en": "Penalty waived: {penalty_amount} {currency}.",
        },
    },
}


def add_client_penalty_templates(apps, schema_editor):
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
        ("bot", "0009_add_client_final_status_templates"),
    ]

    operations = [
        migrations.RunPython(
            add_client_penalty_templates,
            migrations.RunPython.noop,
        ),
    ]
