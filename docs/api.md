# Chefkoch API, as used here

Chefkoch has no public API documentation. Everything below was read from the
website's own traffic (a browser HAR capture, 2026-11-14) and confirmed with
read-only requests. It can change without notice.

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
