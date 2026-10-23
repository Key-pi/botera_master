from django.db import migrations


TEMPLATES = {
    "master.booking_request": {
        "editable": True,
        "placeholders": [
            "client_name",
            "client_phone",
            "date",
            "time",
            "services",
            "fit_note",
            "comment",
        ],
        "translations": {
            "uk": "Нова заявка: {client_name} {client_phone}\n{date} о {time}\n{services}{fit_note}{comment}",
            "ru": "Новая заявка: {client_name} {client_phone}\n{date} в {time}\n{services}{fit_note}{comment}",
            "en": "New booking request: {client_name} {client_phone}\n{date} at {time}\n{services}{fit_note}{comment}",
        },
    },
    "master.booking_request_decline_confirm": {
        "editable": False,
        "placeholders": ["client_name", "date", "time"],
        "translations": {
            "uk": "Відхилити заявку {client_name} на {date} о {time}?",
            "ru": "Отклонить заявку {client_name} на {date} в {time}?",
            "en": "Decline {client_name}'s request for {date} at {time}?",
        },
    },
    "master.booking_request_confirmed": {
        "editable": False,
        "placeholders": ["client_name", "date", "time"],
        "translations": {
            "uk": "Заявку підтверджено: {client_name}, {date} о {time}.",
            "ru": "Заявка подтверждена: {client_name}, {date} в {time}.",
            "en": "Request confirmed: {client_name}, {date} at {time}.",
        },
    },
    "master.booking_request_declined": {
        "editable": False,
        "placeholders": ["client_name", "date", "time"],
        "translations": {
            "uk": "Заявку відхилено: {client_name}, {date} о {time}.",
            "ru": "Заявка отклонена: {client_name}, {date} в {time}.",
            "en": "Request declined: {client_name}, {date} at {time}.",
        },
    },
    "master.booking_request_action_failed": {
        "editable": False,
        "placeholders": [],
        "translations": {
            "uk": "Не вдалося обробити заявку. Її стан міг змінитися або час уже зайнято.",
            "ru": "Не удалось обработать заявку. Её статус мог измениться или время уже занято.",
            "en": "The request could not be processed. Its status may have changed or the time is occupied.",
        },
    },
}


def add_master_booking_request_templates(apps, schema_editor):
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
                    "editable": definition["editable"],
                    "is_required": True,
                    "placeholder_allowlist": placeholders,
                    "sample_context": {
                        placeholder: f"<{placeholder}>" for placeholder in placeholders
                    },
                },
            )


class Migration(migrations.Migration):
    dependencies = [
        ("bot", "0004_sync_booking_input_templates"),
    ]

    operations = [
        migrations.RunPython(
            add_master_booking_request_templates,
            migrations.RunPython.noop,
        ),
    ]
