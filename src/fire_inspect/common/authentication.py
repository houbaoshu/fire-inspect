from rest_framework.authentication import SessionAuthentication


class UnauthorizedSessionAuthentication(SessionAuthentication):
    """Keep Django's CSRF enforcement while making anonymous API access a 401."""

    def authenticate_header(self, request) -> str:
        return "Session"
