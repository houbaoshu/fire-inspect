from django.db import migrations


FORWARD_SQL = (
    """
    CREATE OR REPLACE FUNCTION public.fire_inspect_guard_audit_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF current_user <> pg_get_userbyid(
        (SELECT relation.relowner FROM pg_class AS relation WHERE relation.oid = TG_RELID)
    ) THEN
        RAISE EXCEPTION 'audit events are append-only'
            USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$function$
    """,
    "DROP TRIGGER IF EXISTS audit_auditevent_append_only_guard ON public.audit_auditevent",
    """
    CREATE TRIGGER audit_auditevent_append_only_guard
    BEFORE UPDATE OR DELETE ON public.audit_auditevent
    FOR EACH ROW EXECUTE FUNCTION public.fire_inspect_guard_audit_append_only()
    """,
    "DROP TRIGGER IF EXISTS audit_auditevent_truncate_guard ON public.audit_auditevent",
    """
    CREATE TRIGGER audit_auditevent_truncate_guard
    BEFORE TRUNCATE ON public.audit_auditevent
    FOR EACH STATEMENT EXECUTE FUNCTION public.fire_inspect_guard_audit_append_only()
    """,
    "REVOKE ALL ON FUNCTION public.fire_inspect_guard_audit_append_only() FROM PUBLIC",
)

REVERSE_SQL = (
    "DROP TRIGGER IF EXISTS audit_auditevent_truncate_guard ON public.audit_auditevent",
    "DROP TRIGGER IF EXISTS audit_auditevent_append_only_guard ON public.audit_auditevent",
    "DROP FUNCTION IF EXISTS public.fire_inspect_guard_audit_append_only()",
)


def install_database_guard(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        for statement in FORWARD_SQL:
            schema_editor.execute(statement)


def remove_database_guard(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        for statement in REVERSE_SQL:
            schema_editor.execute(statement)


class Migration(migrations.Migration):
    dependencies = [("audit", "0002_auditevent_hash_timestamp")]

    operations = [
        migrations.RunPython(install_database_guard, remove_database_guard),
    ]
