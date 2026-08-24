from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError

from fire_inspect.jobs.models import ProcessingJob


class Command(BaseCommand):
    help = "Wait until active write jobs have left the running state during a maintenance window."

    def add_arguments(self, parser):
        parser.add_argument("--timeout", type=float, default=300.0)

    def handle(self, *args, **options):
        deadline = time.monotonic() + max(0.0, options["timeout"])
        while ProcessingJob.objects.filter(status=ProcessingJob.Status.RUNNING).exists():
            if time.monotonic() >= deadline:
                raise CommandError("等待 Worker 结束超时，备份未开始。")
            time.sleep(0.5)
        self.stdout.write("workers are idle")
