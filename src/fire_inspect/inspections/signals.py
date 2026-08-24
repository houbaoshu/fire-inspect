from django.dispatch import Signal

# Synchronous by design: receivers participate in the same transaction as the shared update.
inspection_shared_fields_changed = Signal()
inspection_deleted = Signal()
