from django.core.management.base import BaseCommand, CommandError

from fire_inspect.audit.models import AuditEvent


class Command(BaseCommand):
    help = "Verify the permanent audit event hash chain without printing event contents."

    def handle(self, *args, **options):
        previous_hash = ""
        checked = 0
        for event in AuditEvent.objects.order_by("occurred_at", "id").iterator(chunk_size=500):
            checked += 1
            if event.previous_hash != previous_hash or event.event_hash != event.calculate_hash():
                raise CommandError(f"审计链校验失败，事件序号：{checked}")
            previous_hash = event.event_hash
        self.stdout.write(self.style.SUCCESS(f"审计链校验通过，共 {checked} 条。"))
