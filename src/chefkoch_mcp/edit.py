"""Request bodies for creating and changing private recipes, as the website's editor builds them.

The editor (www.chefkoch.de/mein-kochbuch/privatrezepte/) sends the whole recipe on
every save, so a change is: load the recipe, swap the changed fields, send it all
back. Fields the editor leaves undefined are omitted, never null: `scrapeId: null`
makes the API fail with 500 type_error. See docs/api.md.

Ingredients are sent as Chefkoch database entries (`unitId`, `food.id`). Lines that
are new or changed go through Chefkoch's parser; lines that read exactly as
get_recipe shows an existing ingredient keep that ingredient as it is.

Every check here raises ChefkochError before anything is sent, so a refused change
leaves the recipe untouched.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import recipe as rc
from .client import ChefkochError

Difficulty = Literal["simpel", "normal", "pfiffig"]
DIFFICULTY: dict[str, int] = {"simpel": 1, "normal": 2, "pfiffig": 3}

# The editor's single instruction section; it flattens all steps into this one on save.
STEP_SECTION = "Abschnitt 1"

# Plain fields the editor sends on every save, beside ingredients, steps and source.
_FIELDS = (
    "title",
    "subtitle",
    "servings",
    "kCalories",
    "miscellaneousText",
    "difficulty",
    "preparationTime",
    "cookingTime",
    "restingTime",
)


class IngredientGroupInput(BaseModel):
    header: str = Field("", description="Überschrift der Gruppe, z. B. 'Für die Soße'; leer für keine")
    ingredients: list[str] = Field(
        description="Eine Zutat pro Eintrag, wie get_recipe sie zeigt, z. B. '200 g Mehl, gesiebt'"
    )


Parser = Callable[[str], Awaitable[list[dict]]]


def _serialize(i: dict) -> dict:
    """A loaded or parsed ingredient in the shape the save endpoints expect."""
    food = i.get("food") or {}
    # Without a food, get_recipe shows originalInput; keep that rather than send no name.
    name = food.get("nameSingular") or food.get("name") or (i.get("originalInput") or "").strip()
    if not name:
        raise ChefkochError("Eine gespeicherte Zutat hat keinen Namen; das Rezept bitte auf chefkoch.de bearbeiten.")
    return {
        "unitId": (i.get("unit") or {}).get("id"),
        "food": {"id": food.get("id"), "name": name},
        "amount": i.get("amount"),
        "properties": i.get("properties") or [],
    }


def _stored_ingredients(current: dict) -> list[dict]:
    return [i for g in current.get("recipeIngredientGroups") or [] for i in rc.sorted_ingredients(g)]


def _own_foods(current: dict) -> dict[str, dict]:
    """The account's own foods (id `user-…`) in the recipe, by name. The parser does not
    know them and returns food id None, which would create yet another one on save."""
    return {
        (food.get("nameSingular") or "").casefold(): food
        for i in _stored_ingredients(current)
        if str((food := i.get("food") or {}).get("id") or "").startswith("user-")
    }


async def ingredient_groups(
    groups: list[IngredientGroupInput],
    current: dict | None,
    parse: Parser,
    *,
    all_new: bool = False,
) -> list[dict]:
    """Ingredient groups for a save; `current` is the recipe as loaded, None for a new one.

    Refuses a change in which no line matches a stored ingredient, unless `all_new`:
    that is what lines copied from get_recipe with servings look like, and saving them
    would store the scaled amounts as the recipe's own.
    """
    current = current or {}
    stored = _stored_ingredients(current)
    existing: dict[str, list[dict]] = {}
    for i in stored:
        existing.setdefault(rc.ingredient_text(rc.private_ingredient(i)), []).append(i)

    # Reuse first, in order, so duplicate lines each take their own stored ingredient.
    plan = []
    for group in groups:
        lines = [line.strip() for line in group.ingredients if line.strip()]
        plan.append((group.header.strip(), [(line, existing[line].pop(0) if existing.get(line) else None) for line in lines]))

    if not any(items for _, items in plan):
        raise ChefkochError("Keine Zutaten angegeben. Zutaten lassen sich ändern, aber nicht alle entfernen.")
    reused = sum(1 for _, items in plan for _, found in items if found is not None)
    if len(stored) >= 2 and reused == 0 and not all_new:
        raise ChefkochError(
            "Keine der Zutaten entspricht dem gespeicherten Rezept. Falls es mit get_recipe und servings "
            "geladen wurde: ohne servings neu laden und die Zeilen von dort übernehmen. Sollen wirklich "
            "alle Zutaten neu sein, replace_all_ingredients=true setzen."
        )

    own_foods = _own_foods(current)

    async def parse_line(line: str) -> list[dict]:
        found = await parse(line)
        if not found:
            raise ChefkochError(f"Chefkoch versteht die Zutat „{line}“ nicht.")
        for i in found:
            food = i.get("food") or {}
            own = own_foods.get((food.get("nameSingular") or food.get("name") or "").casefold())
            if food.get("id") is None and own:
                i["food"] = own
        return found

    new_lines = list(dict.fromkeys(line for _, items in plan for line, found in items if found is None))
    parsed = dict(zip(new_lines, await asyncio.gather(*(parse_line(line) for line in new_lines))))

    result = []
    for header, items in plan:
        ingredients = [_serialize(i) for line, found in items for i in ([found] if found else parsed[line])]
        if ingredients:
            result.append({"header": header, "ingredients": ingredients})
    return result


def _steps(steps: list[str]) -> list[dict]:
    texts = [s.strip() for s in steps if s.strip()]
    if not texts:
        raise ChefkochError("Keine Zubereitungsschritte angegeben. Schritte lassen sich ändern, aber nicht alle entfernen.")
    return [{"header": STEP_SECTION, "steps": [{"text": t} for t in texts]}]


def _has_legacy_text_only(current: dict, structured: str, legacy: tuple[str, ...]) -> bool:
    has_structured = any(g.get("ingredients") or g.get("steps") for g in current.get(structured) or [])
    return not has_structured and any((current.get(key) or "").strip() for key in legacy)


def _current_ingredient_groups(current: dict) -> list[dict]:
    return [
        {"header": g.get("header") or "", "ingredients": [_serialize(i) for i in rc.sorted_ingredients(g)]}
        for g in sorted(current.get("recipeIngredientGroups") or [], key=lambda g: g.get("position", 0))
    ]


def _current_steps(current: dict) -> list[dict]:
    return [
        {
            "header": g.get("header") or "",
            "steps": [{"text": s.get("text") or ""} for s in sorted(g.get("steps") or [], key=lambda s: s.get("position", 0))],
        }
        for g in sorted(current.get("recipeInstructions") or [], key=lambda g: g.get("position", 0))
    ]


def _source(current: dict) -> dict:
    source = current.get("source") or {}
    return {key: source.get(key) for key in ("name", "url", "siteName", "imageUrl")}


def fields(
    *,
    title: str | None = None,
    servings: int | None = None,
    preparation_time_min: int | None = None,
    cooking_time_min: int | None = None,
    resting_time_min: int | None = None,
    difficulty: Difficulty | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Tool arguments as API fields; None means "not given" and is left out."""
    values = {
        "title": title.strip() if title is not None else None,
        "servings": servings,
        "preparationTime": preparation_time_min,
        "cookingTime": cooking_time_min,
        "restingTime": resting_time_min,
        "difficulty": DIFFICULTY[difficulty] if difficulty else None,
        "miscellaneousText": notes.strip() if notes is not None else None,
    }
    return {k: v for k, v in values.items() if v is not None}


def update_body(
    current: dict,
    changes: dict[str, Any],
    groups: list[dict] | None = None,
    steps: list[str] | None = None,
) -> dict:
    """The whole recipe for PUT: `current` with `changes`; groups/steps replace theirs when given."""
    # Recipes from before Chefkoch's structured format keep ingredients or steps only as
    # free text, which this body cannot carry back. Unseen in this account (2026-11-14).
    if groups is None and _has_legacy_text_only(current, "recipeIngredientGroups", ("ingredientsTextLegacy", "ingredientsText")):
        raise ChefkochError("Dieses Rezept hat die Zutaten nur als Freitext. Bitte alle Zutaten mit angeben (ingredient_groups).")
    if steps is None and _has_legacy_text_only(current, "recipeInstructions", ("instructionsLegacy", "instructions")):
        raise ChefkochError("Dieses Rezept hat die Zubereitung nur als Freitext. Bitte alle Schritte mit angeben (steps).")
    body = {key: current.get(key) for key in _FIELDS} | changes
    body["categoryIds"] = []
    body["source"] = _source(current)
    body["recipeIngredientGroups"] = groups if groups is not None else _current_ingredient_groups(current)
    body["recipeInstructions"] = _steps(steps) if steps is not None else _current_steps(current)
    for key in ("subtype", "scrapeId"):
        if current.get(key) is not None:
            body[key] = current[key]
    return body


def create_body(changes: dict[str, Any], groups: list[dict], steps: list[str]) -> dict:
    """A new recipe for POST, with the editor's defaults for everything not given."""
    defaults = {
        "subtitle": "",
        "servings": 4,
        "kCalories": None,
        "miscellaneousText": "",
        "difficulty": DIFFICULTY["normal"],
        "preparationTime": None,
        "cookingTime": None,
        "restingTime": None,
    }
    return defaults | changes | {
        "recipeIngredientGroups": groups,
        "recipeInstructions": _steps(steps),
        "source": _source({}),
        "version": "2",  # as the editor sends it with structured ingredients
    }
