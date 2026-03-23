from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("qgisfeed", "0018_alter_feedentryreview_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="qgisfeedentry",
            name="action_text",
            field=models.CharField(
                blank=True,
                help_text=(
                    "Optional call-to-action shown only on QGIS 3 (e.g. 'Double-click here to read more'). "
                    "Leave blank if not needed. QGIS 4 opens the URL via a dedicated button so this text is hidden there."
                ),
                max_length=255,
                null=True,
                verbose_name="Call to action text",
            ),
        ),
    ]
