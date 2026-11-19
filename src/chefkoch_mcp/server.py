"""MCP server for one Chefkoch cookbook: reading, plus own recipes and collections."""

from __future__ import annotations

import logging
import os
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, StringConstraints
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import edit
from . import recipe as rc
from .client import ChefkochClient, ChefkochError, is_private

log = logging.getLogger(__name__)

READ_ONLY = {"readOnlyHint": True, "openWorldHint": True}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}
# Overwrites what is there; the previous version is not kept.
OVERWRITE = WRITE | {"destructiveHint": True}

INSTRUCTIONS = """\
Zugriff auf ein privates Chefkoch-Kochbuch: Sammlungen, gespeicherte
Chefkoch-Rezepte und eigene bzw. importierte (private) Rezepte.
Für ein konkretes Rezept erst suchen oder eine Sammlung auflisten, dann get_recipe
mit der id aufrufen. get_recipe rechnet Zutaten auf Wunsch auf andere Portionen um.

Schreiben: create_recipe legt ein eigenes Rezept an, update_recipe ändert eines
(nur private Rezepte, id beginnt mit user-; Chefkoch-Rezepte anderer nicht).
create_collection und set_recipe_collections ordnen Rezepte in Sammlungen.
Vor update_recipe das Rezept mit get_recipe ohne servings laden. Zutaten als Text,
eine pro Eintrag; unveränderte Zutaten genau so übernehmen, wie get_recipe sie zeigt.
Nur ändern, worum gebeten wurde. Eine alte Fassung wird nicht aufbewahrt.
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


class CollectionMembership(BaseModel):
    recipe_id: str
    collections: list[Collection]


IngredientGroups = Annotated[
    list[edit.IngredientGroupInput],
    Field(description="Zutaten in Gruppen; ohne Überschriften eine einzige Gruppe mit header ''"),
]
Steps = Annotated[list[str], Field(description="Zubereitungsschritte, einer pro Eintrag")]
Servings = Annotated[int | None, Field(description="Anzahl Portionen; Mengen werden nicht umgerechnet", ge=1)]
Minutes = Annotated[int | None, Field(ge=0)]
Notes = Annotated[str | None, Field(description="Notizen unter dem Rezept")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


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

    async def saved(recipe_id: str, headline: str) -> ToolResult:
        """The recipe as Chefkoch stored it, so Claude sees what actually arrived."""
        recipe = rc.parse(await client().recipe(recipe_id))
        return ToolResult(
            content=f"{headline}\n\n{rc.to_markdown(recipe)}", structured_content=recipe.model_dump()
        )

    @mcp.tool(annotations=WRITE)
    async def create_recipe(
        title: Text,
        ingredient_groups: IngredientGroups,
        steps: Steps,
        servings: Servings = None,
        preparation_time_min: Minutes = None,
        cooking_time_min: Minutes = None,
        resting_time_min: Minutes = None,
        difficulty: edit.Difficulty | None = None,
        notes: Notes = None,
        collection_ids: Annotated[
            list[str] | None, Field(description="Sammlungen (ids aus list_collections), in die das Rezept soll")
        ] = None,
    ) -> ToolResult:
        """Ein neues eigenes (privates) Rezept im Kochbuch anlegen; nur für dieses Konto sichtbar."""
        c = client()
        changes = edit.fields(
            title=title,
            servings=servings,
            preparation_time_min=preparation_time_min,
            cooking_time_min=cooking_time_min,
            resting_time_min=resting_time_min,
            difficulty=difficulty,
            notes=notes,
        )
        try:
            if collection_ids:
                known = {col["id"] for col in await c.collections()}
                if unknown := [i for i in collection_ids if i not in known]:
                    raise ChefkochError(f"Unbekannte Sammlung(en): {', '.join(unknown)} - ids aus list_collections nehmen.")
            groups = await edit.ingredient_groups(ingredient_groups, None, c.parse_ingredient)
            recipe_id = await c.create_private_recipe(edit.create_body(changes, groups, steps))
        except ChefkochError as e:
            raise ToolError(str(e)) from e
        # From here on the recipe exists: a retry would create a second one.
        try:
            if collection_ids:
                await c.set_recipe_collections(recipe_id, collection_ids)
            return await saved(recipe_id, f"Angelegt, id `{recipe_id}`.")
        except ChefkochError as e:
            raise ToolError(
                f"Das Rezept wurde angelegt (id `{recipe_id}`), danach ging etwas schief: {e} "
                "Nicht noch einmal anlegen; Sammlungen mit set_recipe_collections setzen."
            ) from e

    @mcp.tool(annotations=OVERWRITE)
    async def update_recipe(
        recipe_id: Annotated[str, Field(description="id eines privaten Rezepts (beginnt mit user-)")],
        title: Text | None = None,
        ingredient_groups: Annotated[
            list[edit.IngredientGroupInput] | None,
            Field(description="Ersetzt ALLE Zutaten; unveränderte genau wie bei get_recipe (ohne servings) übernehmen"),
        ] = None,
        replace_all_ingredients: Annotated[
            bool,
            Field(description="Nur wenn wirklich jede Zutat neu ist; sonst lehnt update_recipe eine Liste ab, "
            "in der keine Zeile dem gespeicherten Rezept entspricht"),
        ] = False,
        steps: Annotated[
            list[str] | None,
            Field(description="Ersetzt ALLE Zubereitungsschritte; sie landen in einem Abschnitt, "
            "bisherige Abschnitts-Überschriften entfallen"),
        ] = None,
        servings: Servings = None,
        preparation_time_min: Minutes = None,
        cooking_time_min: Minutes = None,
        resting_time_min: Minutes = None,
        difficulty: edit.Difficulty | None = None,
        notes: Annotated[str | None, Field(description="Ersetzt die Notizen; '' löscht sie")] = None,
    ) -> ToolResult:
        """Ein eigenes (privates) Rezept ändern. Nur übergebene Felder ändern sich, der Rest bleibt."""
        recipe_id = recipe_id.strip()
        if not is_private(recipe_id):
            raise ToolError("Nur eigene Rezepte (id beginnt mit user-) lassen sich ändern.")
        c = client()
        changes = edit.fields(
            title=title,
            servings=servings,
            preparation_time_min=preparation_time_min,
            cooking_time_min=cooking_time_min,
            resting_time_min=resting_time_min,
            difficulty=difficulty,
            notes=notes,
        )
        try:
            current = await c.recipe(recipe_id)
            groups = None
            if ingredient_groups is not None:
                groups = await edit.ingredient_groups(
                    ingredient_groups, current, c.parse_ingredient, all_new=replace_all_ingredients
                )
            await c.update_private_recipe(recipe_id, edit.update_body(current, changes, groups, steps))
            return await saved(recipe_id, "Gespeichert.")
        except ChefkochError as e:
            raise ToolError(str(e)) from e

    @mcp.tool(annotations=WRITE)
    async def create_collection(
        name: Text,
        description: str = "",
    ) -> ToolResult:
        """Eine neue (private) Sammlung im Kochbuch anlegen."""
        try:
            raw = await client().create_collection(name.strip(), description.strip())
        except ChefkochError as e:
            raise ToolError(str(e)) from e
        result = Collection(id=raw["id"], name=raw["name"], recipe_count=raw.get("recipeCount", 0))
        return ToolResult(
            content=f"Sammlung „{result.name}“ angelegt, id `{result.id}`.", structured_content=result.model_dump()
        )

    @mcp.tool(annotations=WRITE | {"idempotentHint": True})
    async def set_recipe_collections(
        recipe_id: Annotated[str, Field(description="id aus list_recipes, search_my_recipes oder create_recipe")],
        add: Annotated[list[str] | None, Field(description="Sammlungs-ids, in die das Rezept zusätzlich soll")] = None,
        remove: Annotated[list[str] | None, Field(description="Sammlungs-ids, aus denen es heraus soll")] = None,
    ) -> ToolResult:
        """Ein Rezept in Sammlungen aufnehmen oder daraus entfernen; andere Sammlungen bleiben, wie sie sind."""
        recipe_id = recipe_id.strip()
        c = client()
        try:
            names = {col["id"]: col for col in await c.collections()}
            if unknown := [i for i in add or [] if i not in names]:
                raise ChefkochError(f"Unbekannte Sammlung(en): {', '.join(unknown)} - ids aus list_collections nehmen.")
            current = await c.recipe_collection_ids(recipe_id)
            drop = set(remove or [])
            wanted = [i for i in dict.fromkeys(current + (add or [])) if i not in drop]
            if wanted != current:
                await c.set_recipe_collections(recipe_id, wanted)
        except ChefkochError as e:
            raise ToolError(str(e)) from e
        collections = [
            Collection(id=i, name=names[i]["name"], recipe_count=names[i].get("recipeCount", 0))
            if i in names
            else Collection(id=i, name=i, recipe_count=0)
            for i in wanted
        ]
        result = CollectionMembership(recipe_id=recipe_id, collections=collections)
        text = ", ".join(f"„{col.name}“" for col in collections)
        return ToolResult(
            content=f"Das Rezept ist jetzt in: {text}." if text else "Das Rezept ist in keiner Sammlung mehr.",
            structured_content=result.model_dump(),
        )

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
