"""MCP server: read-only access to one Chefkoch cookbook."""

from __future__ import annotations

import logging
import os
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import recipe as rc
from .client import ChefkochClient, ChefkochError

log = logging.getLogger(__name__)

READ_ONLY = {"readOnlyHint": True, "openWorldHint": True}

INSTRUCTIONS = """\
Zugriff auf das private Chefkoch-Kochbuch des Haushalts: Sammlungen, gespeicherte
Chefkoch-Rezepte und eigene bzw. importierte (private) Rezepte. Nur lesend.
Für ein konkretes Rezept erst suchen oder eine Sammlung auflisten, dann get_recipe
mit der id aufrufen. get_recipe rechnet Zutaten auf Wunsch auf andere Portionen um.
"""


class Collection(BaseModel):
    id: str
    name: str
    recipe_count: int


class Collections(BaseModel):
    collections: list[Collection]


class RecipeList(BaseModel):
    count: int
    recipes: list[rc.RecipeSummary]


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Environment variable {name} is not set")
    return value


def _auth():
    if os.environ.get("AUTH_DISABLED", "").lower() == "true":
        # Local testing only: anyone who reaches the port reads the cookbook.
        log.warning("AUTH_DISABLED=true - the server accepts every request")
        return None
    from .auth import AllowlistGitHubProvider

    return AllowlistGitHubProvider(
        client_id=_env("GITHUB_CLIENT_ID"),
        client_secret=_env("GITHUB_CLIENT_SECRET"),
        base_url=_env("PUBLIC_BASE_URL"),
        # Read-only profile access is enough to learn the login; the default "user" can write.
        required_scopes=["read:user"],
        # Stable across restarts, so claude.ai's registration and tokens survive a redeploy.
        jwt_signing_key=_env("JWT_SIGNING_KEY"),
        allowed_logins={u.strip() for u in _env("ALLOWED_GITHUB_USERS").split(",") if u.strip()},
    )


_client: ChefkochClient | None = None


def client() -> ChefkochClient:
    global _client
    if _client is None:
        _client = ChefkochClient(_env("CHEFKOCH_USERNAME"), _env("CHEFKOCH_PASSWORD"))
    return _client


def _recipe_lines(recipes: list[rc.RecipeSummary]) -> str:
    return "\n".join(
        f"- {r.title} (id `{r.id}`{', privat' if r.private else ''})"
        + (f" - Notiz: {r.note}" if r.note else "")
        for r in recipes
    )


def build(auth=None) -> FastMCP:
    mcp = FastMCP("Chefkoch", instructions=INSTRUCTIONS, auth=auth, mask_error_details=True)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        # Liveness only: does not log in to Chefkoch.
        return JSONResponse({"status": "ok"})

    @mcp.tool(annotations=READ_ONLY)
    async def list_collections() -> ToolResult:
        """Alle Sammlungen im Chefkoch-Kochbuch, mit Anzahl der Rezepte."""
        try:
            raw = await client().collections()
        except ChefkochError as e:
            raise ToolError(str(e)) from e
        result = Collections(
            collections=[Collection(id=c["id"], name=c["name"], recipe_count=c.get("recipeCount", 0)) for c in raw]
        )
        text = "\n".join(f"- {c.name} ({c.recipe_count} Rezepte, id `{c.id}`)" for c in result.collections)
        return ToolResult(content=text or "Keine Sammlungen.", structured_content=result.model_dump())

    @mcp.tool(annotations=READ_ONLY)
    async def list_recipes(
        collection_id: Annotated[
            str | None,
            Field(description="id aus list_collections; leer = alle gespeicherten Rezepte"),
        ] = None,
    ) -> ToolResult:
        """Rezepte einer Sammlung auflisten, oder ohne collection_id alle gespeicherten Rezepte."""
        try:
            items = await client().recipes(collection_id)
        except ChefkochError as e:
            raise ToolError(str(e)) from e
        recipes = [rc.summary(i) for i in items]
        result = RecipeList(count=len(recipes), recipes=recipes)
        return ToolResult(content=_recipe_lines(recipes) or "Keine Rezepte.", structured_content=result.model_dump())

    @mcp.tool(annotations=READ_ONLY)
    async def search_my_recipes(
        query: Annotated[str, Field(description="Suchbegriff, z. B. eine Zutat oder ein Gericht")],
    ) -> ToolResult:
        """Im eigenen Kochbuch suchen (gespeicherte und private Rezepte), nicht auf ganz Chefkoch."""
        try:
            items = await client().recipes(query=query)
        except ChefkochError as e:
            raise ToolError(str(e)) from e
        recipes = [rc.summary(i) for i in items]
        result = RecipeList(count=len(recipes), recipes=recipes)
        text = _recipe_lines(recipes) or f"Nichts zu „{query}“ im Kochbuch gefunden."
        return ToolResult(content=text, structured_content=result.model_dump())

    @mcp.tool(annotations=READ_ONLY)
    async def get_recipe(
        recipe_id: Annotated[str, Field(description="id aus list_recipes oder search_my_recipes")],
        servings: Annotated[
            float | None,
            Field(description="Auf diese Portionenzahl umrechnen; leer = Originalmenge", gt=0),
        ] = None,
    ) -> ToolResult:
        """Ein Rezept vollständig: Zutaten (optional umgerechnet), Zubereitung, Notizen."""
        try:
            data = await client().recipe(recipe_id.strip())
        except ChefkochError as e:
            raise ToolError(str(e)) from e
        recipe = rc.parse(data, servings)
        return ToolResult(content=rc.to_markdown(recipe), structured_content=recipe.model_dump())

    return mcp


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    # httpx logs every request URL at INFO; fine, but never its headers or bodies.
    client()  # fail at start, not on the first tool call, when credentials are missing
    mcp = build(_auth())
    mcp.run(
        transport="http",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        show_banner=False,
    )
