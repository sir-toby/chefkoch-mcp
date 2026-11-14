"""GitHub login for claude.ai, limited to an allowlist of GitHub accounts.

FastMCP's GitHubProvider accepts any GitHub account that completes the OAuth
flow. This server reads one household's cookbook, so every token is checked
against ALLOWED_GITHUB_USERS as well.
"""

from __future__ import annotations

import logging

from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.providers.github import GitHubProvider

log = logging.getLogger(__name__)


class AllowlistVerifier:
    """Wraps a token verifier and rejects tokens of GitHub users not on the list."""

    def __init__(self, inner, allowed_logins: set[str]) -> None:
        self._inner = inner
        self._allowed = {login.lower() for login in allowed_logins}

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def verify_token(self, token: str) -> AccessToken | None:
        result = await self._inner.verify_token(token)
        if result is None:
            return None
        login = str(result.claims.get("login") or "").lower()
        if login not in self._allowed:
            log.warning("Rejected GitHub user %r: not in ALLOWED_GITHUB_USERS", login)
            return None
        return result


class AllowlistGitHubProvider(GitHubProvider):
    def __init__(self, *, allowed_logins: set[str], **kwargs) -> None:
        if not allowed_logins:
            raise ValueError("ALLOWED_GITHUB_USERS is empty: nobody could log in")
        super().__init__(**kwargs)
        # OAuthProxy validates the upstream GitHub token through this attribute
        # on every request (load_access_token), so wrapping it covers every path.
        self._token_validator = AllowlistVerifier(self._token_validator, allowed_logins)
