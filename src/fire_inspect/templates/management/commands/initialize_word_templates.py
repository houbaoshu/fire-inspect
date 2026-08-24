from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from fire_inspect.templates.services import initialize_authoritative_templates


class Command(BaseCommand):
    help = "安全、幂等地初始化三份权威 Word 模板及确认映射。"

    def add_arguments(self, parser):
        parser.add_argument("--actor", default="")

    def handle(self, *args, **options):
        actor = None
        username = str(options["actor"]).strip()
        if username:
            actor = get_user_model().objects.filter(username=username, role="admin").first()
            if actor is None:
                raise CommandError("指定的管理员账号不存在。")
        initialized = initialize_authoritative_templates(actor=actor)
        for document_type, template in initialized.items():
            self.stdout.write(f"{document_type}: {template.id}")
        self.stdout.write(self.style.SUCCESS("权威 Word 模板初始化完成。"))
