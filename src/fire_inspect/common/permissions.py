from rest_framework.permissions import BasePermission


class IsAdministrator(BasePermission):
    message = "管理员权限必需。"

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.role == "admin")
