"""One recipe shape for Claude, whichever of Chefkoch's two formats it came in.

Public recipes (/recipes/{id}) have `ingredientGroups` with flat names and the
instructions as one text. Private recipes (/cookbooks/…/private-recipes/{id})
have `recipeIngredientGroups` with unit/food objects and `recipeInstructions`
split into steps. Older private recipes may have neither and only free text.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .client import WWW, is_private


class Ingredient(BaseModel):
    amount: float | None  # None: "etwas Salz", "Öl zum Braten"
    unit: str
    name: str
    note: str


class IngredientGroup(BaseModel):
    header: str
    ingredients: list[Ingredient]


class StepGroup(BaseModel):
    header: str
    steps: list[str]


class Recipe(BaseModel):
    id: str
    title: str
    url: str
    private: bool
    servings: float | None
    base_servings: float | None
    total_time_min: int | None
    ingredient_groups: list[IngredientGroup]
    ingredients_text: str  # only when there are no structured ingredients
    step_groups: list[StepGroup]
    notes: str
    tags: list[str]


class RecipeSummary(BaseModel):
    id: str
    title: str
    private: bool
    url: str
    note: str


def summary(item: dict) -> RecipeSummary:
    """A cookbook entry: {recipe: {...}, note: ...}."""
    r = item["recipe"]
    return RecipeSummary(
        id=r["id"],
        title=r["title"],
        private=is_private(r["id"]),
        url=r.get("siteUrl") or "",
        note=item.get("note") or "",
    )


def _clean(text: str | None) -> str:
    return (text or "").strip()


def _plural(obj: dict | None, amount: float | None) -> str:
    if not obj:
        return ""
    use_plural = amount is not None and amount != 1
    return _clean(obj.get("namePlural") if use_plural else obj.get("nameSingular"))


def _amount(value: Any) -> float | None:
    # Chefkoch writes 0 for "no amount given".
    return float(value) if value else None


def _scaled(value: Any, factor: float) -> float | None:
    amount = _amount(value)
    return amount * factor if amount is not None else None


def _private_ingredients(data: dict, factor: float) -> list[IngredientGroup]:
    groups = []
    for group in sorted(data.get("recipeIngredientGroups") or [], key=lambda g: g.get("position", 0)):
        items = [private_ingredient(i, factor) for i in sorted_ingredients(group)]
        groups.append(IngredientGroup(header=_clean(group.get("header")), ingredients=items))
    return groups


def sorted_ingredients(group: dict) -> list[dict]:
    return sorted(group.get("ingredients") or [], key=lambda i: i.get("position", 0))


def private_ingredient(i: dict, factor: float = 1.0) -> Ingredient:
    amount = _scaled(i.get("amount"), factor)  # before choosing singular or plural
    return Ingredient(
        amount=amount,
        unit=_plural(i.get("unit"), amount),
        name=_plural(i.get("food"), amount) or _clean(i.get("originalInput")),
        note=", ".join(i.get("properties") or []),
    )


def _public_ingredients(data: dict, factor: float) -> list[IngredientGroup]:
    return [
        IngredientGroup(
            header=_clean(group.get("header")),
            ingredients=[
                Ingredient(
                    amount=_scaled(i.get("amount"), factor),
                    unit=_clean(i.get("unit")),
                    name=_clean(i.get("name")),
                    note=_clean(i.get("usageInfo")),
                )
                for i in group.get("ingredients") or []
            ],
        )
        for group in data.get("ingredientGroups") or []
    ]


def _steps(data: dict) -> list[StepGroup]:
    structured = data.get("recipeInstructions") or []
    if structured:
        return [
            StepGroup(
                header=_clean(g.get("header")),
                steps=[_clean(s.get("text")) for s in sorted(g.get("steps") or [], key=lambda s: s.get("position", 0))],
            )
            for g in sorted(structured, key=lambda g: g.get("position", 0))
        ]
    text = _clean(data.get("instructions") or data.get("instructionsLegacy"))
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return [StepGroup(header="", steps=paragraphs)] if paragraphs else []


def parse(data: dict, servings: float | None = None) -> Recipe:
    """Normalize a recipe; `servings` scales every amount from the recipe's own count."""
    recipe_id = data["id"]
    base = _amount(data.get("servings"))
    factor = servings / base if servings and base else 1.0
    parse_groups = _private_ingredients if is_private(recipe_id) else _public_ingredients
    groups = [g for g in parse_groups(data, factor) if g.ingredients]

    url = data.get("siteUrl") or ""
    if url.startswith("/"):
        url = WWW + url

    return Recipe(
        id=recipe_id,
        title=_clean(data.get("title")),
        url=url,
        private=is_private(recipe_id),
        servings=servings or base,
        base_servings=base,
        total_time_min=data.get("totalTime") or None,
        ingredient_groups=groups,
        ingredients_text="" if groups else _clean(data.get("ingredientsText") or data.get("ingredientsTextLegacy")),
        step_groups=_steps(data),
        notes=_clean(data.get("miscellaneousText")),
        tags=[t for t in data.get("tags") or [] if isinstance(t, str)],
    )


def format_amount(amount: float | None) -> str:
    if amount is None:
        return ""
    rounded = round(amount, 2)
    text = f"{rounded:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def ingredient_text(i: Ingredient) -> str:
    """One ingredient as get_recipe shows it, e.g. "200 g Mehl, gesiebt"."""
    text = " ".join(p for p in (format_amount(i.amount), i.unit, i.name) if p)
    return f"{text}, {i.note}" if i.note else text


def to_markdown(recipe: Recipe) -> str:
    lines = [f"# {recipe.title}", ""]
    meta = []
    if recipe.servings:
        portions = f"{format_amount(recipe.servings)} {'Portion' if recipe.servings == 1 else 'Portionen'}"
        if recipe.base_servings and recipe.servings != recipe.base_servings:
            portions += f" (umgerechnet von {format_amount(recipe.base_servings)})"
        meta.append(portions)
    if recipe.total_time_min:
        meta.append(f"{recipe.total_time_min} min gesamt")
    meta.append("privates Rezept" if recipe.private else "Chefkoch-Rezept")
    lines += [" · ".join(meta), "", recipe.url, "", "## Zutaten", ""]

    for group in recipe.ingredient_groups:
        if group.header:
            lines += [f"**{group.header}**", ""]
        lines += [f"- {ingredient_text(i)}" for i in group.ingredients]
        lines.append("")
    if recipe.ingredients_text:
        lines += [recipe.ingredients_text, ""]

    lines += ["## Zubereitung", ""]
    for group in recipe.step_groups:
        if group.header:
            lines += [f"**{group.header}**", ""]
        lines += [f"{n}. {step}" for n, step in enumerate(group.steps, 1)]
        lines.append("")

    if recipe.notes:
        lines += ["## Notizen", "", recipe.notes, ""]
    return "\n".join(lines).rstrip() + "\n"
