# {{name}}

**One sentence on what {{name}} does for players.**

---

## ✨ Features

| Feature | What it does |
|---|---|
| **Feature** | What it does |

## 📋 Requirements

| | |
|---|---|
| **Minecraft** | 1.21.1 |
| **Loader** | NeoForge 21.1.x |
| **Java** | 21+ |

<!-- publish:off -->

## Building

Clone [PanzerBuildLogic](https://github.com/PanzerDevOrg/PanzerBuildLogic) next to {{name}} as `panzer-build-logic`:

```bash
git clone https://github.com/PanzerDevOrg/PanzerBuildLogic.git panzer-build-logic
cd {{repo}} && ./gradlew buildAndCollect
```

## Releasing

This README is also the description on Modrinth and CurseForge (everything outside `publish:off` blocks). Add
`docs/changelogs/<version>.md`, set `version` under `[mod]`, then `panzer release {{id}} --push`.

<!-- panzer:license -->
<!-- /panzer:license -->

<!-- publish:on -->

---

<!-- panzer:footer -->
<!-- /panzer:footer -->
