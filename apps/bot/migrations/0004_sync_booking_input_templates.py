from django.db import migrations


FINAL_CONFIRM_TRANSLATIONS = {
    "uk": (
        "Підтвердіть запис:\n{services}\n{date} о {time}\nТривалість: {duration}\n"
        "Сума: {service_total} {currency}{comment}"
    ),
    "ru": (
        "Подтвердите запись:\n{services}\n{date} в {time}\nДлительность: {duration}\n"
        "Сумма: {service_total} {currency}{comment}"
    ),
    "en": (
        "Confirm booking:\n{services}\n{date} at {time}\nDuration: {duration}\n"
        "Total: {service_total} {currency}{comment}"
    ),
}
REQUIRED_FIELD_TRANSLATIONS = {
    "uk": "Щоб продовжити, надішліть: {missing_fields}.",
    "ru": "Чтобы продолжить, отправьте: {missing_fields}.",
    "en": "To continue, send: {missing_fields}.",
}
COMMENT_PROMPT_TRANSLATIONS = {
    "uk": (
        "Надішліть коментар до запису одним текстовим повідомленням "
        "(до {max_length} символів). Коментар необов'язковий."
    ),
    "ru": (
        "Отправьте комментарий к записи одним текстовым сообщением "
        "(до {max_length} символов). Комментарий необязателен."
    ),
    "en": (
        "Send the booking comment as one text message (up to {max_length} characters). "
        "The comment is optional."
    ),
}


def sync_booking_input_templates(apps, schema_editor):
    SystemTemplate = apps.get_model("bot", "SystemTemplate")

    _upsert_templates(
        SystemTemplate,
        key="error.required_field_missing",
        translations=REQUIRED_FIELD_TRANSLATIONS,
        editable=False,
        placeholders=["missing_fields"],
    )
    _upsert_templates(
        SystemTemplate,
        key="booking.comment_prompt",
        translations=COMMENT_PROMPT_TRANSLATIONS,
        editable=True,
        placeholders=["max_length"],
    )

    final_placeholders = [
        "services",
        "date",
        "time",
        "duration",
        "service_total",
        "currency",
        "comment",
    ]
    for language, default_body in FINAL_CONFIRM_TRANSLATIONS.items():
        template, created = SystemTemplate.objects.get_or_create(
            key="booking.final_confirm",
            language=language,
            defaults=_template_defaults(
                body=default_body,
                editable=True,
                placeholders=final_placeholders,
            ),
        )
        if created:
            continue

        # Preserve a platform-customized global body while making the newly
        # required client comment visible before final confirmation.
        if "{comment}" not in template.body:
            template.body = f"{template.body}{{comment}}"
        template.content_type = "message"
        template.editable = True
        template.is_required = True
        template.placeholder_allowlist = final_placeholders
        template.sample_context = _sample_context(final_placeholders)
        template.save(
            update_fields=[
                "body",
                "content_type",
                "editable",
                "is_required",
                "placeholder_allowlist",
                "sample_context",
                "updated_at",
            ]
        )


def _upsert_templates(
    SystemTemplate,
    *,
    key,
    translations,
    editable,
    placeholders,
):
    for language, body in translations.items():
        SystemTemplate.objects.update_or_create(
            key=key,
            language=language,
            defaults=_template_defaults(
                body=body,
                editable=editable,
                placeholders=placeholders,
            ),
        )


def _template_defaults(*, body, editable, placeholders):
    return {
        "body": body,
        "content_type": "message",
        "editable": editable,
        "is_required": True,
        "placeholder_allowlist": placeholders,
        "sample_context": _sample_context(placeholders),
    }


def _sample_context(placeholders):
    return {placeholder: f"<{placeholder}>" for placeholder in placeholders}


class Migration(migrations.Migration):
    dependencies = [
        ("bot", "0003_partnertelegramcontact_partnertelegramcontactlink"),
    ]

    operations = [
        migrations.RunPython(sync_booking_input_templates, migrations.RunPython.noop),
    ]
