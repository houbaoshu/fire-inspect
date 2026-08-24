from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command, get_commands
from django.core.management.base import CommandError

from fire_inspect.accounts.models import User
from fire_inspect.audit.models import AuditEvent

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
STRONG_PASSWORD = "One-Time-Strong-Password-42!"


def test_noninteractive_initial_admin_uses_explicit_password_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TEST_INITIAL_ADMIN_PASSWORD", STRONG_PASSWORD)
    output = StringIO()

    call_command(
        "create_initial_admin",
        username=" Bootstrap.Admin ",
        display_name="初始管理员",
        password_env="TEST_INITIAL_ADMIN_PASSWORD",
        interactive=False,
        stdout=output,
    )

    user = User.objects.get(username="bootstrap.admin")
    assert user.first_name == "初始管理员"
    assert user.role == User.Role.ADMIN
    assert user.status == User.Status.ACTIVE
    assert user.is_staff is True
    assert user.is_superuser is True
    assert user.check_password(STRONG_PASSWORD)
    assert STRONG_PASSWORD not in output.getvalue()
    event = AuditEvent.objects.get(object_type="user", object_id=user.id, action="created")
    assert STRONG_PASSWORD not in event.masked_change_summary


def test_interactive_password_is_hidden_and_confirmed(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "interactive-admin")
    prompts: list[str] = []
    passwords = iter((STRONG_PASSWORD, STRONG_PASSWORD))

    def hidden_password(prompt: str) -> str:
        prompts.append(prompt)
        return next(passwords)

    monkeypatch.setattr("getpass.getpass", hidden_password)
    output = StringIO()
    call_command("create_initial_admin", stdout=output)

    assert len(prompts) == 2
    assert User.objects.get(username="interactive-admin").check_password(STRONG_PASSWORD)
    assert STRONG_PASSWORD not in output.getvalue()


def test_noninteractive_mode_has_no_default_credentials(monkeypatch) -> None:
    monkeypatch.delenv("FIRE_INSPECT_INITIAL_ADMIN_PASSWORD", raising=False)
    with pytest.raises(CommandError, match="--username"):
        call_command("create_initial_admin", interactive=False)
    with pytest.raises(CommandError, match="--password-env"):
        call_command(
            "create_initial_admin",
            username="no-password-admin",
            interactive=False,
        )
    assert User.objects.count() == 0


def test_weak_password_and_second_initial_admin_are_rejected_without_secret_leak(
    monkeypatch,
) -> None:
    monkeypatch.setenv("WEAK_INITIAL_PASSWORD", "tiny")
    with pytest.raises(CommandError) as weak_error:
        call_command(
            "create_initial_admin",
            username="weak-admin",
            password_env="WEAK_INITIAL_PASSWORD",
            interactive=False,
        )
    assert "tiny" not in str(weak_error.value)

    monkeypatch.setenv("STRONG_INITIAL_PASSWORD", STRONG_PASSWORD)
    call_command(
        "create_initial_admin",
        username="first-admin",
        password_env="STRONG_INITIAL_PASSWORD",
        interactive=False,
        stdout=StringIO(),
    )
    with pytest.raises(CommandError, match="已存在管理员"):
        call_command(
            "create_initial_admin",
            username="second-admin",
            password_env="STRONG_INITIAL_PASSWORD",
            interactive=False,
        )
    assert User.objects.filter(role=User.Role.ADMIN).count() == 1


def test_documented_initial_admin_command_is_installed() -> None:
    assert "create_initial_admin" in get_commands()
    for document in (ROOT / "README.md", ROOT / "docs/DEPLOYMENT.md"):
        assert "manage.py create_initial_admin" in document.read_text(encoding="utf-8")
