from __future__ import annotations


class OAuthError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class OAuthNotConfiguredError(OAuthError):
    def __init__(self, platform: str) -> None:
        super().__init__(
            "connection_required",
            f"{platform} OAuth client credentials are not configured in the server environment",
        )
        self.platform = platform


class OAuthStateError(OAuthError):
    def __init__(self, message: str = "OAuth state is invalid or expired") -> None:
        super().__init__("invalid_state", message)


class OAuthExchangeError(OAuthError):
    def __init__(self, platform: str, message: str = "token exchange failed") -> None:
        super().__init__("exchange_failed", message)
        self.platform = platform


def public_oauth_error(exc: Exception) -> str:
    if isinstance(exc, OAuthError):
        return exc.message
    return "oauth request failed"
