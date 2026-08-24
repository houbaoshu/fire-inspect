from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError

from fire_inspect.acceptance import AUTHORITATIVE_ASSETS, file_sha256
from fire_inspect.knowledge.django_models import KnowledgeDocumentRecord
from fire_inspect.knowledge.services import create_knowledge_document


class Command(BaseCommand):
    help = "初始化验收用消防法 PDF（来源始终为待核验）"

    def handle(self, *args, **options):
        path = Path(settings.BASE_DIR) / "testdata/knowledge/reg_ec997c582c9c29a6.pdf"
        expected = next(
            asset.sha256 for asset in AUTHORITATIVE_ASSETS if asset.relative_path.endswith(".pdf")
        )
        if not path.is_file() or file_sha256(path) != expected:
            raise CommandError("消防法测试样本缺失或校验失败")
        if KnowledgeDocumentRecord.objects.filter(sha256=expected).exists():
            self.stdout.write("already initialized")
            return
        upload = SimpleUploadedFile(path.name, path.read_bytes(), content_type="application/pdf")
        record, _ = create_knowledge_document(
            actor=None,
            upload=upload,
            metadata={
                "title": "中华人民共和国消防法（测试样本）",
                "category": "law",
                "source_verification": "pending",
                "validity": "unknown",
            },
        )
        self.stdout.write(f"initialized {record.id}")
