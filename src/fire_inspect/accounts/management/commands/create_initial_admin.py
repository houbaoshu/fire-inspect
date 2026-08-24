from __future__ import annotations

import getpass
import os
import re

from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction

from fire_inspect.accounts.models import User, normalize_username
from fire_inspect.audit.models import AuditEvent

ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Command(BaseCommand):
    help = "Interactively create the one-time initial administrator without default credentials."

    def add_arguments(self, parser):
        parser.add_argument("--username", help="管理员用户名；省略时交互输入。")
        parser.add_argument("--display-name", default="", help="可选的管理员显示姓名。")
        parser.add_argument(
            "--password-env",
            help="从指定的进程环境变量读取密码；不会读取 .env 文件。",
        )
        parser.add_argument(
            "--noinput",
            "--no-input",
            action="store_false",
            dest="interactive",
            help="禁用交互；必须同时提供 --username 和 --password-env。",
        )
        parser.set_defaults(interactive=True)

    def handle(self, *args, **options):
        del args
        interactive = bool(options["interactive"])
        username = self._username(options.get("username"), interactive=interactive)
        display_name = str(options.get("display_name") or "").strip()

        if self._administrator_exists():
            raise CommandError("系统已存在管理员；请登录后通过用户管理功能维护账号。")
        if User.objects.filter(username=username).exists():
            raise CommandError("该用户名已存在；请使用其他管理员用户名。")

        password = self._password(
            environment_name=options.get("password_env"),
            interactive=interactive,
        )
        candidate = User(
            username=username,
            first_name=display_name,
            role=User.Role.ADMIN,
            status=User.Status.ACTIVE,
            is_staff=True,
            is_superuser=True,
        )
        try:
            validate_password(password, user=candidate)
        except ValidationError as exc:
            raise CommandError(f"密码不符合安全要求：{'；'.join(exc.messages)}") from exc

        with transaction.atomic():
            if self._administrator_exists(lock=True):
                raise CommandError("系统已存在管理员；未创建新的账号。")
            if User.objects.select_for_update().filter(username=username).exists():
                raise CommandError("该用户名已存在；未创建新的账号。")
            user = User.objects.create_superuser(
                username=username,
                password=password,
                first_name=display_name,
            )
            AuditEvent.append(
                actor=user,
                object_type="user",
                object_id=user.id,
                inspection_id_snapshot=None,
                action="created",
                masked_change_summary="initial administrator created",
            )

        self.stdout.write(self.style.SUCCESS(f"初始管理员 {user.username} 已创建。"))

    @staticmethod
    def _administrator_exists(*, lock: bool = False) -> bool:
        queryset = User.objects.filter(models.Q(role=User.Role.ADMIN) | models.Q(is_superuser=True))
        if lock:
            queryset = queryset.select_for_update()
        return queryset.exists()

    @staticmethod
    def _username(value, *, interactive: bool) -> str:
        if value is None:
            if not interactive:
                raise CommandError("非交互模式必须提供 --username。")
            value = input("管理员用户名：")
        username = normalize_username(str(value))
        if not username:
            raise CommandError("管理员用户名不能为空。")
        if len(username) > User._meta.get_field("username").max_length:
            raise CommandError("管理员用户名过长。")
        try:
            UnicodeUsernameValidator()(username)
        except ValidationError as exc:
            raise CommandError("管理员用户名格式不正确。") from exc
        return username

    @staticmethod
    def _password(*, environment_name, interactive: bool) -> str:
        if environment_name:
            environment_name = str(environment_name).strip()
            if ENVIRONMENT_NAME.fullmatch(environment_name) is None:
                raise CommandError("--password-env 必须是合法的环境变量名。")
            password = os.environ.get(environment_name)
            if not password:
                raise CommandError("指定的密码环境变量不存在或为空。")
            return password
        if not interactive:
            raise CommandError("非交互模式必须提供 --password-env。")
        password = getpass.getpass("管理员密码：")
        confirmation = getpass.getpass("再次输入管理员密码：")
        if not password:
            raise CommandError("管理员密码不能为空。")
        if password != confirmation:
            raise CommandError("两次输入的管理员密码不一致。")
        return password
