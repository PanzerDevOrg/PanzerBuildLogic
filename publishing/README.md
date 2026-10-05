# publishing

Release and description publishing shared by every PanzerDevOrg mod
(`panzer_publish.py`, run by the reusable workflows in `.github/workflows/`).

## One description, three sites

A mod's **`README.md` is its only description**. Write it for players first;
wrap anything that only belongs on GitHub (building, releasing, repository
layout) in:

```markdown
<!-- publish:off -->
## Building
...
<!-- publish:on -->
```

For each site the publisher then:

| | GitHub | Modrinth | CurseForge |
|---|---|---|---|
| Source | `README.md` as is | README minus `publish:off` blocks | same, rendered to HTML |
| Relative links/images | work | made absolute (GitHub blob/raw URLs) | made absolute |
| Links to a dependency's GitHub repo | kept | its Modrinth page | its CurseForge page (CurseForge forbids links to other download sites) |
| Layout HTML (`<div align>`, `style=`) | partly | partly | unwrapped/dropped; table alignment kept |
| Updated by | git | `mod-description.yml` on each `v*` tag (README pushes only render it) | paste `curseforge.html` (no API exists for CurseForge descriptions) |

Things that render differently somewhere are reported as warnings
(`describe`/`check`): `style=` attributes, `<details>`, `<iframe>`, HTML inside
table rows, headings deeper than `####`.

## Release

On a `v*` tag, the mod's CI builds its jars and calls `mod-release.yml`:

- **GitHub release**: universal jar per Minecraft range, the per-system jars from
  `[publish] platforms` (`windows`, `linux`, `macos`: only that OS's natives; `java`:
  none), sources jars, `SHA256SUMS.txt`, and the changelog with a "which file" table.
- **Modrinth**: the universal jar per Minecraft range, `neoforge`, game versions,
  dependencies by project id, featured flag moved to the new version.
- **CurseForge**: the same jar, with display name `<Mod> <version> for Minecraft <range>`,
  Minecraft versions, NeoForge, `Java 21`/`Java 25` and `Client`/`Server` tags all
  resolved against CurseForge's list first (an unknown one fails the release instead of
  being dropped), and dependencies by slug.

Version numbers follow `<version>-<range label>` (`0.2.0-1.21(.0-.6)`, `0.2.0-1.21.1`).
Re-running is safe for GitHub (assets replaced) and Modrinth (existing versions
skipped); for CurseForge re-run only the failed target (`targets: curseforge`),
since its API cannot list uploaded files.

## Configuration (`mod.stonecutter.properties.toml`)

```toml
[publish]
modrinth_id = "sQphaM3I"
curseforge_id = "1722411"
curseforge_slug = "celeris"          # optional, for links
platforms = ["windows", "linux", "macos", "java"]
client_side = "required"             # Modrinth sides; default from [mod] side
server_side = "required"

[publish.dependencies.celeris]
type = "required"
modrinth = "sQphaM3I"
curseforge = "celeris"               # CurseForge relations use the project slug
github = "PanzerDevOrg/Celeris"      # README links to it become the site's page

["1.21.1"]
game_versions = ["1.21", "1.21.1"]   # what this jar is published for
```

Secrets: `MODRINTH_TOKEN`, `CURSEFORGE_TOKEN` (repository secrets, passed with
`secrets: inherit`). A missing project id skips that site.

## Testing without publishing

```bash
pip install markdown-it-py
python3 publishing/panzer_publish.py preview --mod ../Celeris --out /tmp/celeris-preview
# open /tmp/celeris-preview/preview.html: Modrinth page, CurseForge page, GitHub release, upload table
python3 publishing/panzer_publish.py publish --mod ../Celeris --dry-run --offline
python3 -m unittest discover -s publishing
```

In CI every push runs the release workflow as a dry run and uploads the same
preview as the `release-preview` artifact; `workflow_dispatch` runs it on demand.
