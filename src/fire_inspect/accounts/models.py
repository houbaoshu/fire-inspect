from __future__ import annotations

import unicodedata
import uuid

from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.db import models


def normalize_username(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").strip().casefold()


class UserManager(DjangoUserManager):
    use_in_migrations = True

    def _create_user(self, username, email, password, **extra_fields):
        username = normalize_username(username)
        if not username:
            raise ValueError("用户名不能为空。")
        return super()._create_user(username, email, password, **extra_fields)

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", User.Role.ADMIN)
        extra_fields.setdefault("status", User.Status.ACTIVE)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        INSPECTOR = "inspector", "检查员"
        ADMIN = "admin", "管理员"

    class Status(models.TextChoices):
        ACTIVE = "active", "启用"
        DISABLED = "disabled", "停用"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=150, unique=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.INSPECTOR)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    def save(self, *args, **kwargs):
        self.username = normalize_username(self.username)
        self.is_active = self.status == self.Status.ACTIVE
        if self.role == self.Role.ADMIN:
            self.is_staff = True
        elif not self.is_superuser:
            self.is_staff = False
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.username
