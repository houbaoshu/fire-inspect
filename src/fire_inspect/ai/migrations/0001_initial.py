from __future__ import annotations

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [("materials", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="MaterialVisualAnalysis",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("input_revision", models.PositiveBigIntegerField()),
                ("descriptions", models.JSONField(default=list)),
                ("provider_name", models.CharField(max_length=100)),
                ("model_fingerprint", models.CharField(blank=True, max_length=200)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "material",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="visual_analyses",
                        to="materials.material",
                    ),
                ),
            ],
            options={"ordering": ("material", "input_revision")},
        ),
        migrations.AddConstraint(
            model_name="materialvisualanalysis",
            constraint=models.UniqueConstraint(
                fields=("material", "input_revision"),
                name="unique_visual_analysis_material_revision",
            ),
        ),
    ]
