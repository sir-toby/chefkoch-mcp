# chefkoch-mcp

Remote MCP server that gives Claude access to one Chefkoch.de cookbook: it reads
collections, saved Chefkoch recipes and private (own or imported) recipes, and it
creates and edits private recipes and collections. Added to claude.ai as a custom
connector, it works on web and mobile.

Chefkoch has no official API. This uses the website's own, undocumented one
([docs/api.md](docs/api.md)) and logs in with the account's email and password.
Existing Chefkoch MCP servers (e.g. [smlfg/chefkoch-mcp](https://github.com/smlfg/chefkoch-mcp))
only read public recipes without a login, which is why this exists.

## Tools

| Tool | What it does |
|---|---|
| `list_collections` | All collections with recipe counts |
| `list_recipes` | Recipes of one collection, or all saved recipes |
| `search_my_recipes` | Search the cookbook (Chefkoch's own cookbook search) |
| `get_recipe` | Full recipe, ingredients optionally scaled to other servings |
| `create_recipe` | New private recipe, optionally filed into collections |
| `update_recipe` | Change a private recipe; only the given fields change |
| `create_collection` | New private collection |
| `set_recipe_collections` | Add a recipe to collections or take it out of them |

Writing follows what the website's recipe editor does ([docs/api.md](docs/api.md#writing)):
ingredients are plain text lines that Chefkoch's own parser turns into its database
entries, and a change sends the whole recipe back. Public Chefkoch recipes cannot be
edited, only private ones (id `user-…`). Deleting and photos are not offered.

## Architecture

```
claude.ai ─ HTTPS ─ /mcp  (this server, FastMCP)
                      ├─ OAuth: GitHub login, limited to ALLOWED_GITHUB_USERS
                      └─ Chefkoch: logs in as CHEFKOCH_USERNAME, re-login on 401
```

claude.ai connects from Anthropic's servers, so the server must be publicly
reachable, and **no login page may sit in front of it** (a proxy auth layer would
stop claude.ai). The GitHub OAuth flow is the gate. FastMCP's GitHub provider on
its own admits any GitHub account; `auth.py` adds the allowlist.

## Configuration

See [.env.example](.env.example). For the GitHub OAuth app
(github.com/settings/developers → OAuth Apps → New):

- Homepage URL: `$PUBLIC_BASE_URL`
- Authorization callback URL: `$PUBLIC_BASE_URL/auth/callback`

Mount `/data`: it holds the OAuth state. Without it, every redeploy forces a new
connector login in claude.ai.

## In claude.ai

Settings → Connectors → Add custom connector → URL `$PUBLIC_BASE_URL/mcp`. claude.ai
walks through the GitHub login once; the connector then shows up in the mobile app too.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest            # offline, mocks Chefkoch and GitHub
```

Run locally without OAuth: set `AUTH_DISABLED=true` and the Chefkoch credentials,
then `chefkoch-mcp` serves `http://localhost:8000/mcp`. Never expose that.

## Deployment

CI pushes `sirtoby42/chefkoch-mcp:latest` and `:<sha>` to Docker Hub on every push to
`main` (secrets `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`). Deployment itself is the
`self-host` repo's job.
