from fastmcp.server.auth import AccessToken

from chefkoch_mcp.auth import AllowlistGitHubProvider, AllowlistVerifier


class FakeVerifier:
    required_scopes = ["user"]

    def __init__(self, login):
        self.login = login

    async def verify_token(self, token):
        if self.login is None:
            return None
        return AccessToken(token=token, client_id="1", scopes=["user"], claims={"login": self.login})


async def test_allows_listed_user_case_insensitive():
    assert await AllowlistVerifier(FakeVerifier("Sir-Toby"), {"sir-toby"}).verify_token("t")


async def test_rejects_other_user():
    assert await AllowlistVerifier(FakeVerifier("someone"), {"sir-toby"}).verify_token("t") is None


async def test_passes_through_invalid_token():
    assert await AllowlistVerifier(FakeVerifier(None), {"sir-toby"}).verify_token("t") is None


def test_provider_wraps_its_validator(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMCP_HOME", str(tmp_path))
    provider = AllowlistGitHubProvider(
        allowed_logins={"sir-toby"},
        client_id="id",
        client_secret="secret",
        base_url="https://example.org",
        jwt_signing_key="k" * 32,
        required_scopes=["read:user"],
    )
    assert isinstance(provider._token_validator, AllowlistVerifier)
    assert provider._token_validator.required_scopes == ["read:user"]
