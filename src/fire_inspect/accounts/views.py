from __future__ import annotations

from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from fire_inspect.accounts.models import User, normalize_username
from fire_inspect.accounts.serializers import CurrentUserSerializer, UserAdminSerializer
from fire_inspect.audit.models import AuditEvent
from fire_inspect.common.api import ApiError
from fire_inspect.common.permissions import IsAdministrator
from fire_inspect.common.routing import STRICT_UUID_PATTERN


def _request_id(request) -> str:
    return str(getattr(request, "correlation_id", ""))[:64]


def _audit_authentication(request, *, actor, action: str) -> None:
    AuditEvent.append(
        actor=actor,
        object_type="authentication",
        object_id=getattr(actor, "id", None),
        inspection_id_snapshot=None,
        action=action,
        masked_change_summary=f"authentication event: {action}",
        request_id=_request_id(request),
    )


def _login_throttle_keys(*, remote: str, username: str) -> tuple[str, str]:
    return f"login-fail:ip:{remote}", f"login-fail:account:{username}"


def _increment_login_failure(keys: tuple[str, str]) -> None:
    for key in keys:
        cache.set(key, int(cache.get(key, 0)) + 1, timeout=300)


class CsrfView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        return Response({"csrf_token": get_token(request)})


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @method_decorator(csrf_protect)
    def post(self, request):
        username = normalize_username(request.data.get("username", ""))
        password = request.data.get("password")
        remote = request.META.get("REMOTE_ADDR", "unknown")
        ip_key, account_key = _login_throttle_keys(remote=remote, username=username)
        if int(cache.get(ip_key, 0)) >= 50 or int(cache.get(account_key, 0)) >= 10:
            _audit_authentication(request, actor=None, action="login_rate_limited")
            raise ApiError(
                code="login_rate_limited",
                message="登录尝试过多，请稍后再试。",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        user = None
        if username and isinstance(password, str) and password:
            user = authenticate(request, username=username, password=password)
        if user is None or user.status != User.Status.ACTIVE:
            _increment_login_failure((ip_key, account_key))
            _audit_authentication(request, actor=None, action="login_failed")
            raise ApiError(
                code="invalid_credentials",
                message="用户名或密码错误。",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        cache.delete(account_key)
        login(request, user)
        _audit_authentication(request, actor=user, action="login_succeeded")
        return Response({"user": CurrentUserSerializer(user).data})


class LogoutView(APIView):
    def post(self, request):
        actor = request.user
        _audit_authentication(request, actor=actor, action="logout")
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CurrentUserView(APIView):
    def get(self, request):
        return Response({"user": CurrentUserSerializer(request.user).data})


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.order_by("username")
    serializer_class = UserAdminSerializer
    permission_classes = [IsAdministrator]
    http_method_names = ["get", "post", "patch", "head", "options"]
    lookup_value_regex = STRICT_UUID_PATTERN

    def perform_create(self, serializer):
        user = serializer.save()
        AuditEvent.append(
            actor=self.request.user,
            object_type="user",
            object_id=user.id,
            inspection_id_snapshot=None,
            action="created",
            masked_change_summary="user created",
            request_id=_request_id(self.request),
        )

    def perform_update(self, serializer):
        changed_fields = sorted(
            field
            for field in self.request.data
            if field in {"username", "display_name", "password", "role", "status"}
        )
        user = serializer.save()
        AuditEvent.append(
            actor=self.request.user,
            object_type="user",
            object_id=user.id,
            inspection_id_snapshot=None,
            action="updated",
            masked_change_summary=f"user updated: fields={','.join(changed_fields)}",
            request_id=_request_id(self.request),
        )
