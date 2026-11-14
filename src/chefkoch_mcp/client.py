"""Client for Chefkoch's unofficial API, logged in as one user.

Login is two steps on www.chefkoch.de: authenticate sets a session cookie, and
/benutzer/me hands out the api_token that api.chefkoch.de expects in the
X-Chefkoch-Api-Token header. The token carries no expiry, so the client keeps it
until the API answers 401 and then logs in again, once. See docs/api.md.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

WWW = "https://www.chefkoch.de"
API = "https://api.chefkoch.de/v2"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
)
PAGE_LIMIT = 100  # the largest page size the API accepts


class ChefkochError(Exception):
    """Something the user should read: login failed, recipe not found, API changed."""


class ChefkochClient:
    def __init__(
        self,
        username: str,
        password: str,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._http = http or httpx.AsyncClient(timeout=20)
        self._http.headers.update(
            {"User-Agent": USER_AGENT, "Accept": "application/json", "Origin": WWW}
        )
        self._token: str | None = None
        self._user_id: str | None = None
        self._login_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def cookbook_id(self) -> str:
        if self._user_id is None:
            raise RuntimeError("not logged in")
        return f"user-{self._user_id}"

    async def login(self) -> None:
        # Never log the request or response: both can carry credentials.
        self._http.cookies.clear()
        response = await self._http.post(
            f"{WWW}/benutzer/api/authenticate",
            json={
                "identifier": self._username,
                "password": self._password,
                "referer": f"{WWW}/",
                "context": "login/init",
            },
            headers={"Referer": f"{WWW}/benutzer/einloggen"},
        )
        if response.status_code == 401:
            raise ChefkochError("Chefkoch-Login abgelehnt: E-Mail oder Passwort falsch.")
        if response.status_code != 200:
            raise ChefkochError(f"Chefkoch-Login fehlgeschlagen (HTTP {response.status_code}).")

        me = await self._http.get(f"{WWW}/benutzer/me")
        if me.status_code != 200:
            raise ChefkochError(f"Chefkoch-Login unvollständig (/benutzer/me: HTTP {me.status_code}).")
        data = me.json()
        try:
            self._token = data["api_token"]
            self._user_id = data["id"]
        except KeyError as e:
            raise ChefkochError(f"Chefkoch-Antwort ohne {e} - hat sich die API geändert?") from e
        log.info("Logged in to Chefkoch")

    async def _ensure_login(self, stale_token: str | None = None) -> None:
        async with self._login_lock:
            # Another request may have logged in while this one waited.
            if self._token is None or self._token == stale_token:
                await self.login()

    async def _request(self, method: str, path: str, *, json: Any = None, **params: Any) -> Any:
        """Call an api.chefkoch.de path as the logged-in user, re-logging in once on 401."""
        await self._ensure_login()
        for attempt in (1, 2):
            token = self._token
            try:
                response = await self._http.request(
                    method,
                    f"{API}{path}",
                    json=json,
                    params={k: v for k, v in params.items() if v is not None},
                    headers={"X-Chefkoch-Api-Token": token or ""},
                )
            except httpx.HTTPError as e:
                unclear = "" if method == "GET" else " Ob die Änderung angekommen ist, ist unklar."
                raise ChefkochError(f"Keine Antwort von Chefkoch ({type(e).__name__}) für {method} {path}.{unclear}") from e
            if response.status_code == 401 and attempt == 1:
                log.info("Chefkoch token rejected, logging in again")
                await self._ensure_login(stale_token=token)
                continue
            if response.status_code == 404:
                raise ChefkochError("Nicht gefunden - stimmt die id?")
            data = _json(response)
            # Writes answer {"notification": {"hasErrors": …, "messages": [{"identifier": …}]}}.
            notification = data.get("notification") if isinstance(data, dict) else None
            if response.status_code not in (200, 201) or (notification or {}).get("hasErrors"):
                reason = _error_identifier(notification)
                raise ChefkochError(
                    f"Chefkoch-API antwortet mit HTTP {response.status_code} für {method} {path}"
                    + (f" ({reason})" if reason else "")
                )
            if data is None:
                raise ChefkochError(f"Chefkoch-Antwort für {method} {path} ist kein JSON - hat sich die API geändert?")
            return data
        raise ChefkochError("Chefkoch lehnt auch den frischen Login ab.")

    async def get(self, path: str, **params: Any) -> Any:
        return await self._request("GET", path, **params)

    # --- cookbook ---------------------------------------------------------

    async def _all_pages(self, path: str, **params: Any) -> list[dict]:
        results: list[dict] = []
        offset = 0
        while True:
            page = await self.get(path, offset=offset, limit=PAGE_LIMIT, **params)
            results += page["results"]
            offset += PAGE_LIMIT
            if offset >= page["count"] or not page["results"]:
                return results

    async def collections(self) -> list[dict]:
        await self._ensure_login()
        # order/orderBy as the website sends them: alphabetical by name.
        return await self._all_pages(
            f"/cookbooks/{self.cookbook_id}/collections", order=1, orderBy=2
        )

    async def recipes(self, collection_id: str | None = None, query: str | None = None) -> list[dict]:
        """Saved recipes, of one collection or of the whole cookbook; `query` searches server-side."""
        await self._ensure_login()
        base = f"/cookbooks/{self.cookbook_id}"
        path = f"{base}/collections/{collection_id}/recipes" if collection_id else f"{base}/recipes"
        return await self._all_pages(path, query=query)

    async def recipe(self, recipe_id: str) -> dict:
        """One recipe. Private ones (id `user-…`) come from the cookbook, which
        has the structured ingredients and steps; /recipes/{id} has only text for them."""
        await self._ensure_login()
        if is_private(recipe_id):
            return await self.get(f"/cookbooks/{self.cookbook_id}/private-recipes/{recipe_id}")
        return await self.get(f"/recipes/{recipe_id}")

    # --- writing ----------------------------------------------------------
    # Request bodies as the website's editor sends them, see docs/api.md.

    async def parse_ingredient(self, line: str) -> list[dict]:
        """Chefkoch's own parser: "200 g Mehl, gesiebt" -> amount, unit and food with their ids.
        Usually one result; unknown foods come back with food id None."""
        data = await self._request("POST", "/cookbooks/ingredients/parse", json={"query": line})
        return _key(data, "results")

    async def create_private_recipe(self, body: dict) -> str:
        await self._ensure_login()
        data = await self._request("POST", f"/cookbooks/{self.cookbook_id}/private-recipes", json=body)
        return _first_message(data)["recipe"]["id"]

    async def update_private_recipe(self, recipe_id: str, body: dict) -> None:
        """Replaces the whole recipe; send every field, not only the changed ones."""
        await self._ensure_login()
        await self._request("PUT", f"/cookbooks/{self.cookbook_id}/private-recipes/{recipe_id}", json=body)

    async def create_collection(self, name: str, description: str = "") -> dict:
        await self._ensure_login()
        body = {"name": name, "descriptionText": description} if description else {"name": name}
        data = await self._request("POST", f"/cookbooks/{self.cookbook_id}/collections", json=body)
        return _first_message(data)["collection"]

    async def recipe_collection_ids(self, recipe_id: str) -> list[str]:
        await self._ensure_login()
        data = await self.get(f"/cookbooks/{self.cookbook_id}/recipes/{recipe_id}/collections")
        return _key(data, "collectionIds")

    async def set_recipe_collections(self, recipe_id: str, collection_ids: list[str]) -> None:
        """`collection_ids` is the complete set: the recipe leaves every collection not in it."""
        await self._ensure_login()
        await self._request(
            "PATCH",
            f"/cookbooks/{self.cookbook_id}/saved-recipes",
            json={"recipeIds": [recipe_id], "collectionIds": collection_ids},
        )


def is_private(recipe_id: str) -> bool:
    return recipe_id.startswith("user-")


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _error_identifier(notification: dict | None) -> str:
    messages = (notification or {}).get("messages") or []
    return ", ".join(m["identifier"] for m in messages if isinstance(m, dict) and m.get("identifier"))


def _first_message(data: dict) -> dict:
    try:
        return data["notification"]["messages"][0]
    except (KeyError, IndexError, TypeError) as e:
        raise ChefkochError("Chefkoch-Antwort ohne Bestätigung - hat sich die API geändert?") from e


def _key(data: Any, key: str) -> Any:
    try:
        return data[key]
    except (KeyError, TypeError) as e:
        raise ChefkochError(f"Chefkoch-Antwort ohne {key} - hat sich die API geändert?") from e
