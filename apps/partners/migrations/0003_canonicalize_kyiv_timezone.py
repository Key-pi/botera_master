from django.db import migrations, models


def canonicalize_kyiv_timezone(apps, schema_editor):
    partner_model = apps.get_model("partners", "Partner")
    partner_model.objects.filter(timezone="Europe/Kiev").update(timezone="Europe/Kyiv")


def restore_legacy_kyiv_timezone(apps, schema_editor):
    partner_model = apps.get_model("partners", "Partner")
    partner_model.objects.filter(timezone="Europe/Kyiv").update(timezone="Europe/Kiev")


class Migration(migrations.Migration):
    dependencies = [
        ("partners", "0002_alter_partner_options_alter_partner_created_at_and_more"),
    ]

    operations = [
        migrations.RunPython(
            canonicalize_kyiv_timezone,
            restore_legacy_kyiv_timezone,
        ),
        migrations.AlterField(
            model_name="partner",
            name="timezone",
            field=models.CharField(
                default="Europe/Kyiv",
                max_length=64,
                verbose_name="timezone",
            ),
        ),
    ]
