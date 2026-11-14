from chefkoch_mcp import recipe as rc

PRIVATE = {
    "id": "user-11111111-2222-3333-4444-555555555555",
    "title": "Linsensuppe",
    "servings": 2,
    "siteUrl": "https://www.chefkoch.de/mein-kochbuch/privatrezepte/user-1",
    "recipeIngredientGroups": [
        {
            "header": None,
            "position": 0,
            "ingredients": [
                {
                    "position": 1,
                    "amount": 1.0,
                    "unit": None,
                    "food": {"nameSingular": "Zwiebel", "namePlural": "Zwiebeln"},
                    "properties": ["gewürfelt"],
                },
                {
                    "position": 0,
                    "amount": 150.0,
                    "unit": {"nameSingular": "g", "namePlural": "g"},
                    "food": {"nameSingular": "Linse", "namePlural": "Linsen"},
                    "properties": [],
                },
                {
                    "position": 2,
                    "amount": 0,
                    "unit": None,
                    "food": {"nameSingular": "Salz", "namePlural": "Salz"},
                    "properties": [],
                },
            ],
        }
    ],
    "recipeInstructions": [
        {"header": "Abschnitt 1", "position": 0, "steps": [{"position": 1, "text": "Kochen."}, {"position": 0, "text": "Schneiden."}]}
    ],
    "miscellaneousText": "Mit Zitrone.",
}

PUBLIC = {
    "id": "123",
    "title": "Hefezopf",
    "servings": 1,
    "totalTime": 75,
    "siteUrl": "https://www.chefkoch.de/rezepte/123/Hefezopf.html",
    "ingredientGroups": [
        {"header": " ", "ingredients": [{"name": "Mehl", "unit": "kg", "amount": 1.0, "usageInfo": "Type 405"}]}
    ],
    "instructions": "Mehl sieben.\n\nKneten.",
}


def test_private_recipe_scaled_with_plural():
    r = rc.parse(PRIVATE, servings=4)
    onion, = [i for i in r.ingredient_groups[0].ingredients if i.name.startswith("Zwiebel")]
    assert (onion.amount, onion.name, onion.note) == (2.0, "Zwiebeln", "gewürfelt")
    assert [i.name for i in r.ingredient_groups[0].ingredients] == ["Linsen", "Zwiebeln", "Salz"]
    assert r.ingredient_groups[0].ingredients[2].amount is None
    assert r.step_groups[0].steps == ["Schneiden.", "Kochen."]
    assert r.private and r.servings == 4 and r.base_servings == 2


def test_public_recipe():
    r = rc.parse(PUBLIC)
    assert r.ingredient_groups[0].header == ""
    assert r.ingredient_groups[0].ingredients[0].note == "Type 405"
    assert r.step_groups[0].steps == ["Mehl sieben.", "Kneten."]
    assert not r.private


def test_free_text_fallback():
    r = rc.parse({"id": "user-x", "title": "Alt", "ingredientsText": "1 Ei", "instructions": "Braten."})
    assert r.ingredient_groups == [] and r.ingredients_text == "1 Ei"


def test_markdown():
    md = rc.to_markdown(rc.parse(PRIVATE, servings=4))
    assert "4 Portionen (umgerechnet von 2)" in md
    assert "- 300 g Linsen" in md
    assert "- 2 Zwiebeln, gewürfelt" in md
    assert "- Salz\n" in md
    assert "1. Schneiden." in md
    assert "1 Portion ·" in rc.to_markdown(rc.parse(PUBLIC))


def test_format_amount():
    assert rc.format_amount(0.5) == "0,5"
    assert rc.format_amount(1 / 3) == "0,33"
    assert rc.format_amount(2.0) == "2"
