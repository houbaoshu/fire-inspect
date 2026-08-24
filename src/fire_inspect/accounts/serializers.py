from __future__ import annotations

from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from fire_inspect.accounts.models import User, normalize_username


class CurrentUserSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(format="hex_verbose", read_only=True)

    class Meta:
        model = User
        fields = ("id", "username", "role", "status")


class UserAdminSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(format="hex_verbose", read_only=True)
    display_name = serializers.CharField(
        source="first_name", required=False, allow_blank=True, max_length=150
    )
    password = serializers.CharField(
        write_only=True, required=False, validators=[validate_password]
    )

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "display_name",
            "password",
            "role",
            "status",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def validate_username(self, value: str) -> str:
        value = normalize_username(value)
        if not value:
            raise serializers.ValidationError("用户名不能为空。")
        query = User.objects.filter(username=value)
        if self.instance:
            query = query.exclude(pk=self.instance.pk)
        if query.exists():
            raise serializers.ValidationError("用户名已存在。")
        return value

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        if not password:
            raise serializers.ValidationError({"password": "创建用户时必须设置密码。"})
        return User.objects.create_user(password=password, **validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance
