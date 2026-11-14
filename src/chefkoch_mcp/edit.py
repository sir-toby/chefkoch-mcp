"""Request bodies for creating and changing private recipes, as the website's editor builds them.

The editor (www.chefkoch.de/mein-kochbuch/privatrezepte/) sends the whole recipe on
every save, so a change is: load the recipe, swap the changed fields, send it all
back. Fields the editor leaves undefined are omitted, never null: `scrapeId: null`
makes the API fail with 500 type_error. See docs/api.md.

Ingredients are sent as Chefkoch database entries (`unitId`, `food.id`). Lines that
are new or changed go through Chefkoch's parser; lines that read exactly as
get_recipe shows an existing ingredient keep that ingredient as it is.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import recipe as rc

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
    return {
        "unitId": (i.get("unit") or {}).get("id"),
        "food": {"id": food.get("id"), "name": food.get("nameSingular") or food.get("name")},
        "amount": i.get("amount"),
        "properties": i.get("properties") or [],
    }


def _existing_by_line(current: dict) -> dict[str, list[dict]]:
    by_line: dict[str, list[dict]] = {}
    for group in current.get("recipeIngredientGroups") or []:
        for i in rc.sorted_ingredients(group):
            by_line.setdefault(rc.ingredient_text(rc.private_ingredient(i)), []).append(i)
    return by_line


async def ingredient_groups(groups: list[IngredientGroupInput], current: dict | None, parse: Parser) -> list[dict]:
    """Ingredient groups for a save; `current` is the recipe as loaded, None for a new one."""
    existing = _existing_by_line(current or {})

    async def resolve(line: str) -> list[dict]:
        if existing.get(line):
            return [existing[line].pop(0)]
        return await parse(line)

    result = []
    for group in groups:
        lines = [line.strip() for line in group.ingredients if line.strip()]
        resolved = await asyncio.gather(*(resolve(line) for line in lines))
        ingredients = [_serialize(i) for found in resolved for i in found]
        if ingredients or group.header.strip():
            result.append({"header": group.header.strip(), "ingredients": ingredients})
    return result


def _steps(steps: list[str]) -> list[dict]:
    texts = [s.strip() for s in steps if s.strip()]
    return [{"header": STEP_SECTION, "steps": [{"text": t} for t in texts]}] if texts else []


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
