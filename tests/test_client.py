import httpx
import pytest
import respx

from chefkoch_mcp.client import API, WWW, ChefkochClient, ChefkochError

UID = "0123456789abcdef0123456789abcdef"


def mock_login(router: respx.Router, tokens: list[str]) -> respx.Route:
    router.post(f"{WWW}/benutzer/api/authenticate").respond(200, json={"redirectUrl": f"{WWW}/"})
    return router.get(f"{WWW}/benutzer/me").mock(
        side_effect=[httpx.Response(200, json={"id": UID, "api_token": t}) for t in tokens]
    )


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as r:
        yield r


async def test_sends_token_from_login(router):
    mock_login(router, ["tok-1"])
    route = router.get(f"{API}/cookbooks/user-{UID}/collections").respond(200, json={"count": 0, "results": []})

    assert await ChefkochClient("a@b.de", "pw").collections() == []
    assert route.calls.last.request.headers["X-Chefkoch-Api-Token"] == "tok-1"


async def test_logs_in_again_once_on_401(router):
    me = mock_login(router, ["old", "new"])
    route = router.get(f"{API}/recipes/42").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json={"id": "42"})]
    )

    assert await ChefkochClient("a@b.de", "pw").recipe("42") == {"id": "42"}
    assert me.call_count == 2
    assert route.calls.last.request.headers["X-Chefkoch-Api-Token"] == "new"


async def test_gives_up_after_second_401(router):
    mock_login(router, ["old", "new"])
    router.get(f"{API}/recipes/42").respond(401)

    with pytest.raises(ChefkochError):
        await ChefkochClient("a@b.de", "pw").recipe("42")


async def test_bad_credentials(router):
    router.post(f"{WWW}/benutzer/api/authenticate").respond(401)

    with pytest.raises(ChefkochError, match="falsch"):
        await ChefkochClient("a@b.de", "wrong").collections()


async def test_private_recipe_comes_from_cookbook(router):
    mock_login(router, ["tok"])
    route = router.get(f"{API}/cookbooks/user-{UID}/private-recipes/user-abc").respond(200, json={"id": "user-abc"})

    await ChefkochClient("a@b.de", "pw").recipe("user-abc")
    assert route.called


async def test_pages_through_everything(router):
    mock_login(router, ["tok"])
    page = [{"recipe": {"id": str(n)}} for n in range(100)]
    route = router.get(f"{API}/cookbooks/user-{UID}/recipes").mock(
        side_effect=[
            httpx.Response(200, json={"count": 150, "results": page}),
            httpx.Response(200, json={"count": 150, "results": page[:50]}),
        ]
    )

    assert len(await ChefkochClient("a@b.de", "pw").recipes()) == 150
    assert route.calls[1].request.url.params["offset"] == "100"
