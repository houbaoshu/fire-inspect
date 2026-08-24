import hashlib
import json

import django.utils.timezone
from django.db import migrations, models


def rehash_existing_events(apps, schema_editor):
    AuditEvent = apps.get_model("audit", "AuditEvent")
    previous_hash = ""
    for event in AuditEvent.objects.order_by("occurred_at", "id").iterator(chunk_size=500):
        payload = {
            "id": str(event.id),
            "actor_label": event.actor_label,
            "occurred_at": event.occurred_at.isoformat(),
            "object_type": event.object_type,
            "object_id": str(event.object_id or ""),
            "inspection_id_snapshot": str(event.inspection_id_snapshot or ""),
            "action": event.action,
            "reason": event.reason,
            "masked_change_summary": event.masked_change_summary,
            "request_id": event.request_id,
            "previous_hash": previous_hash,
        }
        event_hash = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        AuditEvent.objects.filter(pk=event.pk).update(
            previous_hash=previous_hash,
            event_hash=event_hash,
        )
        previous_hash = event_hash


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="auditevent",
            name="occurred_at",
            field=models.DateTimeField(
                default=django.utils.timezone.now,
                editable=False,
            ),
        ),
        migrations.RunPython(rehash_existing_events, migrations.RunPython.noop),
    ]
