from chefkoch_mcp import edit

LOADED = {
    "id": "user-1",
    "title": "Plätzchen",
    "subtitle": "",
    "servings": 2,
    "kCalories": 0,
    "difficulty": 1,
    "preparationTime": 10,
    "cookingTime": 20,
    "restingTime": None,
    "miscellaneousText": "Alt",
    "subtype": "ocr",
    "scrapeId": None,
    "source": {"name": None, "url": None},
    "imageCount": 2,
    "recipeIngredientGroups": [
        {
            "id": 7,
            "header": "",
            "position": 0,
            "ingredients": [
                {
                    "id": 1,
                    "position": 0,
                    "amount": 500.0,
                    "unit": {"id": 3, "nameSingular": "g", "namePlural": "g"},
                    "food": {"id": "71", "nameSingular": "Mehl", "namePlural": "Mehl"},
                    "properties": [],
                },
                {
                    "id": 2,
                    "position": 1,
                    "amount": 1.0,
                    "unit": None,
                    "food": {"id": "user-abc", "nameSingular": "Omas Gewürz", "namePlural": "Omas Gewürz"},
                    "properties": ["fein"],
                },
            ],
        }
    ],
    "recipeInstructions": [
        {"header": "Abschnitt 1", "position": 0, "steps": [{"id": 9, "position": 0, "text": "Backen."}]}
    ],
}

PARSED_EGG = {
    "amount": 2.0,
    "unit": None,
    "food": {"id": "36", "name": "Eier", "nameSingular": "Ei", "namePlural": "Eier"},
    "properties": [],
}


async def test_unchanged_lines_keep_their_ingredient_new_lines_are_parsed():
    parsed = []

    async def parse(line):
        parsed.append(line)
        return [PARSED_EGG]

    groups = [
        edit.IngredientGroupInput(ingredients=["500 g Mehl", " 2 Eier ", ""]),
        edit.IngredientGroupInput(header="Würze", ingredients=["1 Omas Gewürz, fein"]),
    ]
    result = await edit.ingredient_groups(groups, LOADED, parse)

    assert parsed == ["2 Eier"]
    assert result == [
        {
            "header": "",
            "ingredients": [
                {"unitId": 3, "food": {"id": "71", "name": "Mehl"}, "amount": 500.0, "properties": []},
                {"unitId": None, "food": {"id": "36", "name": "Ei"}, "amount": 2.0, "properties": []},
            ],
        },
        {
            "header": "Würze",
            "ingredients": [
                {"unitId": None, "food": {"id": "user-abc", "name": "Omas Gewürz"}, "amount": 1.0, "properties": ["fein"]}
            ],
        },
    ]


async def test_changed_amount_is_parsed_again():
    async def parse(line):
        return [{**LOADED["recipeIngredientGroups"][0]["ingredients"][0], "amount": 250.0}]

    result = await edit.ingredient_groups([edit.IngredientGroupInput(ingredients=["250 g Mehl"])], LOADED, parse)
    assert result[0]["ingredients"][0]["amount"] == 250.0


def test_update_keeps_everything_not_changed():
    body = edit.update_body(LOADED, edit.fields(title=" Kekse ", notes=""))

    assert body["title"] == "Kekse"
    assert body["miscellaneousText"] == ""
    assert (body["servings"], body["preparationTime"], body["difficulty"]) == (2, 10, 1)
    assert body["recipeIngredientGroups"][0]["ingredients"][1]["food"] == {"id": "user-abc", "name": "Omas Gewürz"}
    assert body["recipeInstructions"] == [{"header": "Abschnitt 1", "steps": [{"text": "Backen."}]}]
    assert body["subtype"] == "ocr"
    # null here makes Chefkoch answer 500 type_error
    assert "scrapeId" not in body
    assert body["source"] == {"name": None, "url": None, "siteName": None, "imageUrl": None}
    assert "imageCount" not in body and "id" not in body


def test_update_replaces_steps():
    body = edit.update_body(LOADED, {}, steps=["Kneten.", " ", "Backen."])
    assert body["recipeInstructions"] == [{"header": "Abschnitt 1", "steps": [{"text": "Kneten."}, {"text": "Backen."}]}]


def test_create_body():
    body = edit.create_body(edit.fields(title="Suppe", difficulty="pfiffig"), [], ["Kochen."])
    assert body["version"] == "2"
    assert (body["title"], body["difficulty"], body["servings"]) == ("Suppe", 3, 4)
    assert "scrapeId" not in body and "subtype" not in body
