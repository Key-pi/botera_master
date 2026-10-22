from django.db import migrations


TEMPLATES = {
    "master.booking_request_proposal_dates": {
        "placeholders": ["client_name", "selected_count", "selection_limit"],
        "translations": {
            "uk": "Оберіть дати для {client_name}. Вибрано {selected_count} з {selection_limit} варіантів.",
            "ru": "Выберите даты для {client_name}. Выбрано {selected_count} из {selection_limit} вариантов.",
            "en": "Choose dates for {client_name}. Selected {selected_count} of {selection_limit} options.",
        },
    },
    "master.booking_request_proposal_slots": {
        "placeholders": ["date", "selected_count", "selection_limit"],
        "translations": {
            "uk": "Оберіть час на {date}. Вибрано {selected_count} з {selection_limit} варіантів.",
            "ru": "Выберите время на {date}. Выбрано {selected_count} из {selection_limit} вариантов.",
            "en": "Choose a time on {date}. Selected {selected_count} of {selection_limit} options.",
        },
    },
    "master.booking_request_proposal_review": {
        "placeholders": ["client_name", "options"],
        "translations": {
            "uk": "Надіслати {client_name} ці варіанти?\n\n{options}",
            "ru": "Отправить {client_name} эти варианты?\n\n{options}",
            "en": "Send these options to {client_name}?\n\n{options}",
        },
    },
    "master.booking_request_proposal_sent": {
        "placeholders": ["client_name", "option_count"],
        "translations": {
            "uk": "Клієнту {client_name} надіслано варіанти часу: {option_count}.",
            "ru": "Клиенту {client_name} отправлены варианты времени: {option_count}.",
            "en": "Sent {option_count} time options to {client_name}.",
        },
    },
    "master.booking_request_proposal_no_dates": {
        "placeholders": ["client_name"],
        "translations": {
            "uk": "Для заявки {client_name} немає доступних альтернативних дат.",
            "ru": "Для заявки {client_name} нет доступных альтернативных дат.",
            "en": "No alternative dates are available for {client_name}'s request.",
        },
    },
    "master.booking_request_proposal_no_slots": {
        "placeholders": ["date"],
        "translations": {
            "uk": "На {date} більше немає доступного часу. Оберіть іншу дату.",
            "ru": "На {date} больше нет доступного времени. Выберите другую дату.",
            "en": "No times remain available on {date}. Choose another date.",
        },
    },
}


def add_master_request_proposal_templates(apps, schema_editor):
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
                    "editable": False,
                    "is_required": True,
                    "placeholder_allowlist": placeholders,
                    "sample_context": {
                        placeholder: f"<{placeholder}>" for placeholder in placeholders
                    },
                },
            )


class Migration(migrations.Migration):
    dependencies = [
        ("bot", "0005_add_master_booking_request_templates"),
    ]

    operations = [
        migrations.RunPython(
            add_master_request_proposal_templates,
            migrations.RunPython.noop,
        ),
    ]
