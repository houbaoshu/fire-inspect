from django.urls import re_path
from rest_framework.routers import SimpleRouter

from fire_inspect.accounts.views import (
    CsrfView,
    CurrentUserView,
    LoginView,
    LogoutView,
    UserViewSet,
)

router = SimpleRouter(trailing_slash=False)
router.register("users", UserViewSet, basename="user")
router.register("admin/users", UserViewSet, basename="admin-user")
router.trailing_slash = "/?"

urlpatterns = [
    re_path(r"^auth/csrf/?$", CsrfView.as_view(), name="auth-csrf"),
    re_path(r"^auth/login/?$", LoginView.as_view(), name="auth-login"),
    re_path(r"^auth/logout/?$", LogoutView.as_view(), name="auth-logout"),
    re_path(r"^auth/me/?$", CurrentUserView.as_view(), name="auth-me"),
    *router.urls,
]
