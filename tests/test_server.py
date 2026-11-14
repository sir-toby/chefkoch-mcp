import json

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from chefkoch_mcp import server
from chefkoch_mcp.client import API, ChefkochClient

from .test_client import UID, mock_login
from .test_edit import LOADED

CB = f"{API}/cookbooks/user-{UID}"


@pytest.fixture
def router(monkeypatch):
    monkeypatch.setattr(server, "_client", ChefkochClient("a@b.de", "pw"))
    with respx.mock(assert_all_called=False) as r:
        mock_login(r, ["tok"])
        yield r


async def call(tool, **args):
    async with Client(server.build()) as c:
        return await c.call_tool(tool, args)


async def test_update_recipe_sends_whole_recipe_with_change(router):
    router.get(f"{CB}/private-recipes/user-1").respond(200, json=LOADED)
    put = router.put(f"{CB}/private-recipes/user-1").respond(201, json={"notification": {"hasErrors": False}})

    result = await call("update_recipe", recipe_id="user-1", servings=4)

    body = json.loads(put.calls.last.request.content)
    assert body["servings"] == 4 and body["title"] == "Plätzchen"
    assert len(body["recipeIngredientGroups"][0]["ingredients"]) == 2
    assert result.content[0].text.startswith("Gespeichert.")


async def test_update_refuses_public_recipe(router):
    with pytest.raises(ToolError, match="Nur eigene Rezepte"):
        await call("update_recipe", recipe_id="123", title="x")


async def test_set_recipe_collections_adds_to_current(router):
    router.get(f"{CB}/recipes/user-1/collections").respond(200, json={"collectionIds": ["c1", "c2"]})
    patch = router.patch(f"{CB}/saved-recipes").respond(200, json={"notification": {"hasErrors": False}})
    router.get(f"{CB}/collections").respond(
        200, json={"count": 2, "results": [{"id": "c2", "name": "Kekse"}, {"id": "c3", "name": "Weihnachten"}]}
    )

    result = await call("set_recipe_collections", recipe_id="user-1", add=["c3", "c2"], remove=["c1"])

    assert json.loads(patch.calls.last.request.content) == {"recipeIds": ["user-1"], "collectionIds": ["c2", "c3"]}
    assert result.content[0].text == "Das Rezept ist jetzt in: „Kekse“, „Weihnachten“."


async def test_create_recipe_parses_lines_and_files_it(router):
    router.post(f"{API}/cookbooks/ingredients/parse").mock(
        side_effect=lambda req: httpx.Response(
            200, json={"results": [{"amount": 1.0, "unit": None, "food": {"id": "36", "nameSingular": "Ei"}, "properties": []}]}
        )
    )
    post = router.post(f"{CB}/private-recipes").respond(
        201, json={"notification": {"hasErrors": False, "messages": [{"recipe": {"id": "user-new"}}]}}
    )
    patch = router.patch(f"{CB}/saved-recipes").respond(200, json={"notification": {"hasErrors": False}})
    router.get(f"{CB}/collections").respond(200, json={"count": 1, "results": [{"id": "c1", "name": "Kekse"}]})
    router.get(f"{CB}/private-recipes/user-new").respond(200, json={**LOADED, "id": "user-new", "title": "Ei"})

    result = await call(
        "create_recipe",
        title="Ei",
        ingredient_groups=[{"ingredients": ["1 Ei"]}],
        steps=["Kochen."],
        collection_ids=["c1"],
    )

    body = json.loads(post.calls.last.request.content)
    assert body["recipeIngredientGroups"] == [
        {"header": "", "ingredients": [{"unitId": None, "food": {"id": "36", "name": "Ei"}, "amount": 1.0, "properties": []}]}
    ]
    assert json.loads(patch.calls.last.request.content)["collectionIds"] == ["c1"]
    assert "id `user-new`" in result.content[0].text


async def test_create_recipe_failure_afterwards_names_the_new_id(router):
    router.get(f"{CB}/collections").respond(200, json={"count": 1, "results": [{"id": "c1", "name": "Kekse"}]})
    router.post(f"{API}/cookbooks/ingredients/parse").respond(
        200, json={"results": [{"amount": 1.0, "unit": None, "food": {"id": "36", "nameSingular": "Ei"}, "properties": []}]}
    )
    router.post(f"{CB}/private-recipes").respond(
        201, json={"notification": {"hasErrors": False, "messages": [{"recipe": {"id": "user-new"}}]}}
    )
    router.patch(f"{CB}/saved-recipes").respond(500)

    with pytest.raises(ToolError, match="user-new.*Nicht noch einmal"):
        await call("create_recipe", title="Ei", ingredient_groups=[{"ingredients": ["1 Ei"]}], steps=["Kochen."], collection_ids=["c1"])


async def test_create_recipe_checks_collections_first(router):
    router.get(f"{CB}/collections").respond(200, json={"count": 0, "results": []})
    post = router.post(f"{CB}/private-recipes")

    with pytest.raises(ToolError, match="Unbekannte Sammlung"):
        await call("create_recipe", title="Ei", ingredient_groups=[{"ingredients": ["1 Ei"]}], steps=["Kochen."], collection_ids=["c9"])
    assert not post.called


async def test_blank_title_is_refused(router):
    load = router.get(f"{CB}/private-recipes/user-1").respond(200, json=LOADED)

    with pytest.raises(ToolError, match="(?i)validation|at least 1"):
        await call("update_recipe", recipe_id="user-1", title="   ")
    assert not load.called
