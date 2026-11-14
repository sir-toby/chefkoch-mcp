# Chefkoch API, as used here

Chefkoch has no public API documentation. The reading part below was read from the
website's own traffic (a browser HAR capture, 2026-11-14) and confirmed with
read-only requests. The [writing](#writing) part comes from the website's
JavaScript and was tested with throwaway recipes. Both can change without notice.

## Login

Two hosts, two steps.

1. `POST https://www.chefkoch.de/benutzer/api/authenticate`

   ```json
   {"identifier": "<email>", "password": "<password>", "referer": "https://www.chefkoch.de/", "context": "login/init"}
   ```

   `200 {"redirectUrl": …}` and two cookies, `authUserSession` and `chefkoch_whoami`.
   Wrong credentials: `401 {"notification": {"messages": [{"identifier": "bad_credentials"}]}}`.

2. `GET https://www.chefkoch.de/benutzer/me` with those cookies returns the user,
   among other fields `id` (32 hex chars) and **`api_token`**.

`api_token` has the form `<user id>-<unix time of login>-<32 hex signature>`. The
timestamp is when it was issued, not when it expires; nothing in the token or in
`/benutzer/me` states a lifetime. The client therefore keeps it until a 401 and
then logs in again.

The website runs a script challenge before login (`/_fs-ch-…/check`, `pat`,
`fst-post-back`). A plain HTTP login does not trip it (tested 2026-11-14 from a
residential connection). If that changes, login is the first thing to break.

## API

Base `https://api.chefkoch.de/v2`, header `X-Chefkoch-Api-Token: <api_token>`.
Without the token, private data answers `401`. The cookbook id is `user-<user id>`.

Lists are paged with `offset` and `limit`; `limit` up to 100 works (the site uses
6 or 12). Responses look like `{"meta": null, "count": <total>, "results": [...]}`.

| Purpose | Endpoint | Notes |
|---|---|---|
| Collections | `GET /cookbooks/{cb}/collections` | `order=1&orderBy=2` = by name, as the site sends it. Items: `id` (`user-<uid>-<n>`), `name`, `recipeCount` |
| One collection | `GET /cookbooks/{cb}/collections/{collectionId}` | |
| Recipes of a collection | `GET /cookbooks/{cb}/collections/{collectionId}/recipes` | also returns `category` (the collection) |
| All saved recipes | `GET /cookbooks/{cb}/recipes` | `order=0&orderBy=2` on the site. **`query=`** searches server-side |
| Followed public collections | `GET /cookbooks/{cb}/followed-public-collections` | not used |
| Cookbook state / feature flags | `GET /cookbooks/{cb}/state` | not used |
| Recipe | `GET /recipes/{id}` | public and private recipes |
| Private recipe, structured | `GET /cookbooks/{cb}/private-recipes/{id}` | see below |

Cookbook list items are `{"recipe": {...summary...}, "note": "…", "createdAt": …}`.

### Two kinds of recipe

| | Public | Private (own, imported, OCR) |
|---|---|---|
| id | numeric string, e.g. `378801124204434` | `user-<uuid>` |
| `type` / `subtype` | `0` / `default` | `1` / `default` or `ocr` |
| Ingredients | `ingredientGroups[].ingredients[]`: `name`, `unit`, `amount`, `usageInfo` | `/recipes/{id}`: only `ingredientsText` (free text). `/private-recipes/{id}`: `recipeIngredientGroups[].ingredients[]` with `amount`, `unit{nameSingular,namePlural}`, `food{nameSingular,namePlural}`, `properties[]`, `position` |
| Steps | `instructions`, one text, paragraphs separated by blank lines | `recipeInstructions[]`: `header`, `steps[].text`, `position` |

Both: `servings`, `totalTime`/`preparationTime`/`cookingTime`/`restingTime` (minutes),
`miscellaneousText`, `tags`, `siteUrl`. An `amount` of `0` means none was given
("Salz und Pfeffer"). Older private recipes may still have only
`ingredientsTextLegacy`/`instructionsLegacy`.

## Writing

Read from the website's own JavaScript on 2026-11-14: the cookbook app
(`/mein-kochbuch/assets/main-*.js`) and the private recipe editor
(`/mein-kochbuch/privatrezepte/assets/index-*.js`). Every call below was tried
against the real account with throwaway recipes, which were deleted afterwards,
except `sourceCategoryId` and the revert, which are only from the JavaScript.
Same base URL and token header as for reading. Writes answer
`{"notification": {"hasErrors": false, "messages": [{"type": "success", "identifier": …, …}]}}`;
errors carry the reason in `identifier`, e.g. `500 … "type_error"`.

| Purpose | Endpoint | Body |
|---|---|---|
| Parse ingredient text | `POST /cookbooks/ingredients/parse` | `{"query": "200 g Mehl, gesiebt"}` → `results[]` |
| Create private recipe | `POST /cookbooks/{cb}/private-recipes` | whole recipe plus `"version": "2"`; `201`, new id in `messages[0].recipe.id` |
| Change private recipe | `PUT /cookbooks/{cb}/private-recipes/{id}` | whole recipe plus `"categoryIds": []` |
| Create collection | `POST /cookbooks/{cb}/collections` | `{"name", "descriptionText"}`; `messages[0].collection` |
| Rename collection | `PUT /cookbooks/{cb}/collections/{id}` | `{"name", "descriptionText", "editorialImage", "editorialHeadline", "editorialText", "stageImage"}` (strings) |
| Delete collection | `DELETE /cookbooks/{cb}/collections/{id}?withRecipes=false` | |
| Collections of a recipe | `GET /cookbooks/{cb}/recipes/{id}/collections` | → `{"collectionIds": [...]}` |
| Set collections of recipes | `PATCH /cookbooks/{cb}/saved-recipes` | `{"recipeIds": [...], "collectionIds": [...]}`, the **complete** set: a recipe leaves every collection not listed |
| Remove from cookbook | `POST /cookbooks/{cb}/recipes/remove` | `{"recipeIds": [...]}`, with `sourceCategoryId` only from that collection; answers a `revert.id` for `POST /cookbooks/{cb}/revert/{revertId}` |

Only `parse`, the two recipe calls and the collection calls are used here.

### The recipe body

The editor sends the whole recipe on every save, create and change alike:

```json
{
  "title": "…", "subtitle": "", "servings": 4, "kCalories": null,
  "difficulty": 2, "preparationTime": 10, "cookingTime": 20, "restingTime": null,
  "miscellaneousText": "notes",
  "source": {"name": null, "url": null, "siteName": null, "imageUrl": null},
  "recipeIngredientGroups": [
    {"header": "", "ingredients": [
      {"unitId": 3, "food": {"id": "71", "name": "Mehl"}, "amount": 500.0, "properties": ["gesiebt"]}
    ]}
  ],
  "recipeInstructions": [{"header": "Abschnitt 1", "steps": [{"text": "…"}]}]
}
```

- `difficulty`: 1 simpel, 2 normal (the editor's default), 3 pfiffig.
- `subtype` and `scrapeId` are sent only when the recipe has them. The editor omits
  undefined fields, and **`"scrapeId": null` fails with `500 type_error`**.
- Ingredients reference Chefkoch's database: `unitId` (`null` for none) and `food.id`.
  The loaded recipe has `unit{id,…}` and `food{id, nameSingular,…}`, which map onto
  these. A `food.id` of `null` is accepted: Chefkoch then creates a food of the
  account's own (id `user-<uuid>`), which stays after the recipe is gone.
- The editor puts all steps into one section, `Abschnitt 1`.
- Images are not part of the body (`…/private-recipes/{id}/images`, not used), and a
  `PUT` leaves them alone.
- Changing `servings` does not rescale amounts.

### Two ingredient parsers

`/cookbooks/ingredients/parse` is what the editor uses for typed input. It reads
"1/2 TL Zimt", "1 Dose Tomaten (400 g)" (`properties: ["400 g"]`) or "etwas
Petersilie" (unit `etwas`, no amount) reliably, also several lines at once.
`/cookbooks/ingredients/parse-text` converts old free-text recipes and guesses from
context: the same "500 g Mehl" came back with and without amount and unit depending
on the other lines. Not used.

