from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import fire_inspect.knowledge.django_models
import uuid


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="KnowledgeDocumentRecord",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=300)),
                ("category", models.CharField(choices=[("law", "正式法律法规"), ("standard", "标准规范"), ("local_rule", "地方规定"), ("internal", "内部制度"), ("other", "其他材料")], max_length=20)),
                ("issuer", models.CharField(blank=True, max_length=300)),
                ("source_reference", models.CharField(blank=True, max_length=1000)),
                ("version", models.CharField(blank=True, max_length=100)),
                ("publication_date", models.DateField(blank=True, null=True)),
                ("source_verification", models.CharField(choices=[("pending", "待核验"), ("verified", "已核验"), ("rejected", "已拒绝")], default="pending", max_length=16)),
                ("validity", models.CharField(choices=[("unknown", "未知"), ("effective", "现行有效"), ("expired", "已失效")], default="unknown", max_length=16)),
                ("original_file", models.FileField(upload_to=fire_inspect.knowledge.django_models.knowledge_upload_path)),
                ("mime_type", models.CharField(max_length=100)),
                ("sha256", models.CharField(max_length=64, unique=True)),
                ("byte_size", models.PositiveBigIntegerField()),
                ("processing_status", models.CharField(choices=[("pending", "待处理"), ("succeeded", "成功"), ("failed", "失败")], default="pending", max_length=16)),
                ("safe_error_message", models.CharField(blank=True, max_length=500)),
                ("revision", models.PositiveBigIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("uploaded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="uploaded_knowledge_documents", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("title", "created_at")},
        ),
        migrations.CreateModel(
            name="KnowledgeClauseRecord",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("sequence", models.PositiveIntegerField()),
                ("chapter", models.CharField(blank=True, max_length=200)),
                ("section", models.CharField(blank=True, max_length=200)),
                ("clause_identifier", models.CharField(max_length=100)),
                ("heading", models.CharField(blank=True, max_length=300)),
                ("content", models.TextField()),
                ("page_start", models.PositiveIntegerField()),
                ("page_end", models.PositiveIntegerField()),
                ("source_locator", models.JSONField(default=dict)),
                ("normalized_text", models.TextField()),
                ("parser_version", models.CharField(default="coordinate-v1", max_length=50)),
                ("document", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="clauses", to="knowledge.knowledgedocumentrecord")),
            ],
            options={"ordering": ("document", "sequence")},
        ),
        migrations.CreateModel(
            name="KnowledgeEmbeddingRecord",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("model_fingerprint", models.CharField(max_length=200)),
                ("dimensions", models.PositiveIntegerField()),
                ("values", models.JSONField(default=list)),
                ("status", models.CharField(choices=[("pending", "待处理"), ("succeeded", "成功"), ("failed", "失败")], default="pending", max_length=16)),
                ("safe_error_message", models.CharField(blank=True, max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("clause", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="embeddings", to="knowledge.knowledgeclauserecord")),
            ],
        ),
        migrations.AddConstraint(
            model_name="knowledgeclauserecord",
            constraint=models.UniqueConstraint(fields=("document", "sequence"), name="knowledge_document_clause_sequence_unique"),
        ),
        migrations.AddIndex(
            model_name="knowledgeclauserecord",
            index=models.Index(fields=["document", "clause_identifier"], name="knowledge_k_documen_c02b08_idx"),
        ),
        migrations.AddConstraint(
            model_name="knowledgeembeddingrecord",
            constraint=models.UniqueConstraint(fields=("clause", "model_fingerprint"), name="knowledge_clause_embedding_model_unique"),
        ),
    ]

