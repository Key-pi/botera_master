from django.db import migrations


TEMPLATES = {
    "reminder.manual_retry_sent": {
        "placeholders": [],
        "translations": {
            "uk": "Нагадування успішно надіслано клієнту.",
            "ru": "Напоминание успешно отправлено клиенту.",
            "en": "The reminder was sent to the client.",
        },
    },
    "reminder.manual_retry_failed": {
        "placeholders": ["failure_reason"],
        "translations": {
            "uk": "Повторно надіслати нагадування не вдалося. Причина: {failure_reason}.",
            "ru": "Повторно отправить напоминание не удалось. Причина: {failure_reason}.",
            "en": "The reminder could not be resent. Reason: {failure_reason}.",
        },
    },
    "reminder.manual_retry_skipped": {
        "placeholders": [],
        "translations": {
            "uk": "Нагадування вже неактуальне, тому повторне надсилання пропущено.",
            "ru": "Напоминание уже неактуально, поэтому повторная отправка пропущена.",
            "en": "The reminder is no longer relevant, so the resend was skipped.",
        },
    },
    "reminder.failed_client_summary": {
        "placeholders": [
            "client_name",
            "client_phone",
            "visit_count",
            "future_count",
            "penalty_count",
            "telegram_status",
        ],
        "translations": {
            "uk": "Клієнт: {client_name}\nТелефон: {client_phone}\nВізитів: {visit_count}\nМайбутніх записів: {future_count}\nАктивних штрафів: {penalty_count}\nTelegram: {telegram_status}",
            "ru": "Клиент: {client_name}\nТелефон: {client_phone}\nВизитов: {visit_count}\nБудущих записей: {future_count}\nАктивных штрафов: {penalty_count}\nTelegram: {telegram_status}",
            "en": "Client: {client_name}\nPhone: {client_phone}\nVisits: {visit_count}\nFuture bookings: {future_count}\nActive penalties: {penalty_count}\nTelegram: {telegram_status}",
        },
    },
    "messaging.master_reply_unavailable": {
        "placeholders": ["client_name"],
        "translations": {
            "uk": "Неможливо написати клієнту {client_name}: зв'язок через Telegram зараз недоступний.",
            "ru": "Невозможно написать клиенту {client_name}: связь через Telegram сейчас недоступна.",
            "en": "Cannot message {client_name}: the Telegram connection is currently unavailable.",
        },
    },
}


def add_failed_reminder_action_templates(apps, schema_editor):
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
        ("bot", "0006_add_master_request_proposal_templates"),
    ]

    operations = [
        migrations.RunPython(
            add_failed_reminder_action_templates,
            migrations.RunPython.noop,
        ),
    ]
