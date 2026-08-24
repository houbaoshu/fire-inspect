from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("knowledge", "0002_rename_knowledge_k_documen_c02b08_idx_knowledge_k_documen_54eea7_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="knowledgeembeddingrecord",
            name="input_revision",
            field=models.PositiveBigIntegerField(default=1),
        ),
    ]
