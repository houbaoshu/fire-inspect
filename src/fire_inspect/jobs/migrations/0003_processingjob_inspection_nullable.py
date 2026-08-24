import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inspections", "0001_initial"),
        ("jobs", "0002_processingjob_last_retry_idempotency_key"),
    ]

    operations = [
        migrations.AlterField(
            model_name="processingjob",
            name="inspection",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="processing_jobs",
                to="inspections.inspection",
            ),
        ),
    ]
