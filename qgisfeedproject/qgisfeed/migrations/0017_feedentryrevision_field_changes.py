from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("qgisfeed", "0016_qgisfeedentry_reviewers_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="feedentryrevision",
            name="field_changes",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Structured list of per-field old→new changes",
                verbose_name="Field Changes",
            ),
        ),
    ]
