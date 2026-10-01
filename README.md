# Panzer Build Logic

Shared composite build for PanzerDevOrg's NeoForge mods. One place for Stonecutter
multi-version setup, per-mod version overrides, optional JVM modules, external tool
management (oxipng, ...), and early TOML validation -- so an individual mod's
`build.gradle.kts` stays close to just the things that actually differ between mods.

## Structure

```
panzer-build-logic/
├── common.stonecutter.properties.toml   # shared config: [jvm], [dependencies],
│                                         # [plugins], [neoforge], [tools.*], version
│                                         # blocks ["1.21.x"], [overrides.*],
│                                         # [stonecutter.profiles.*]
├── templates/
│   ├── gradle.properties                # shared gradle.properties template
│   └── settings.gradle.kts              # template for a mod's own settings.gradle.kts
├── scaffold-mod.sh                      # creates a new mod from scratch
├── sync-mod.sh                          # re-syncs an existing mod's shared files
├── .github/workflows/publish.yml        # CI: build on every push, publish on tag
└── src/main/kotlin/
    ├── com/panzer/gradle/
    │   ├── TomlBlockReader.kt           # [table] block parser, no Stonecutter dependency
    │   ├── VersionOverrides.kt          # resolves overrides.<mod_id>."<version>" over the base block
    │   ├── ToolVersions.kt              # catalog of [tools.*]
    │   ├── ExternalToolResolver.kt      # generic cache/download/extract for external binaries
    │   ├── PanzerDiagnostics.kt         # early validation of the merged TOML
    │   ├── ModBuildProperties.kt        # typed reader of the merged stonecutter.properties.toml
    │   ├── PanzerModExtension.kt        # public `panzerMod { }` extension
    │   ├── NeoForgeMutexPlugin.kt       # BuildService serializing createMinecraftArtifacts
    │   ├── OptimizeTexturesTask.kt      # PNG optimization via oxipng (uses ExternalToolResolver)
    │   └── JsonMinifier.kt
    ├── panzer.neoforge-mod.gradle.kts          # main convention plugin
    └── panzer.neoforge-mod-example.gradle.kts  # convention plugin for :example submodules
```

## What's shared vs. what's per-mod

| Lives in... | Contents |
|---|---|
| `panzer-build-logic/common.stonecutter.properties.toml` | `[jvm]`, `[dependencies]`, `[plugins]`, `[neoforge]`, `[tools.*]`, version blocks `["1.21.x"]`, `[overrides.<mod_id>."1.21.x"]`, `[stonecutter.profiles.*]` |
| `<mod>/mod.stonecutter.properties.toml` | `[mod]`, `[stonecutter]` (which versions this mod targets), and any mod-specific table |
| `<mod>/stonecutter.properties.toml` | Generated automatically by `settings.gradle.kts` from the two files above. Never edit by hand, never commit it. |

The merge works table-by-table: if a `[table]` exists in `mod.stonecutter.properties.toml`,
that whole table wins over the common one. If it doesn't exist in the mod, it's inherited
as-is from the common file. `[overrides.*]` is the one exception: it lives in the
**common** file, but only applies to the `mod_id` it names, field by field, on top of the
base version block -- not a whole-table replacement.

## How a mod consumes this repo

Clone `panzer-build-logic` **next to** each mod (`../panzer-build-logic`). That sibling
checkout is required: a mod's `settings.gradle.kts` reads
`../panzer-build-logic/common.stonecutter.properties.toml` before any plugin is
resolved, and `includeBuild`s the plugin from the same folder. Edit anything here and
every mod picks it up on its next Gradle run, with no publish or version bump.

CI does the same: each mod's workflow checks out `PanzerDevOrg/PanzerBuildLogic`
beside the mod under `$GITHUB_WORKSPACE`.

A mod's `build.gradle.kts` stays exactly `plugins { id("panzer.neoforge-mod") }`, with no
version literal and no per-mod branching.

The plugin is additionally published as a static Maven repository
(`https://panzerdevorg.github.io/PanzerBuildLogic/maven`, served from the `gh-pages`
branch, no token needed). The template's `settings.gradle.kts` uses it when no sibling
folder exists, but that path still needs the common TOML, so it is not yet a complete
replacement for the sibling checkout.

### Cutting a release

Push a tag matching `v*` (e.g. `v0.2.0`). CI (`.github/workflows/publish.yml`) builds,
publishes the plugin markers + jar to a throwaway `build/publish-repo` directory, and
copies that onto the `gh-pages` branch under `/maven`. Every push and PR also just
*builds* (no publish) so a broken change is caught before it reaches anyone.

After a release, bump `panzerBuildLogicVersion` in `templates/gradle.properties` and
run `sync-mod.sh` against each mod. Mods built from a sibling checkout never read that
property.

## Setting up a new mod

**Automated:** `./scaffold-mod.sh <destination-path> <mod_id> [mod_group]` from inside
`panzer-build-logic/`, then follow the printed next steps (`sync-mod.sh`, review
`mod.stonecutter.properties.toml`).

**By hand:**

1. Create `mod.stonecutter.properties.toml` at the mod's root with `[mod]` (`id`,
   `version`, `group`) and `[stonecutter]` (`versions = [...]` or `profile = "..."`,
   plus `vcs_version`).
2. Add `/stonecutter.properties.toml` to the mod's `.gitignore` -- it's generated.
3. Copy `templates/settings.gradle.kts` and `templates/gradle.properties`.
4. In the mod's `build.gradle.kts`: `plugins { id("panzer.neoforge-mod") }` plus
   whatever's specific to that mod.

## `[stonecutter]`: choosing which Minecraft versions to build

Three ways to say which versions a mod targets, in priority order:

```toml
[stonecutter]
versions = ["1.21.1", "1.21.4"]   # explicit list -- always wins if present
```

```toml
[stonecutter]
profile = "legacy"                 # named list from [stonecutter.profiles.legacy]
extra_versions = ["26.1"]          # optional: add versions on top of the profile
exclude_versions = ["1.21.4"]      # optional: remove versions from the profile
```

Profiles are defined once in `common.stonecutter.properties.toml`
(`[stonecutter.profiles.legacy]`, `[stonecutter.profiles.current]`, ...) so mods don't
repeat the same version list. A mod can also declare its own
`[stonecutter.profiles.*]` if it needs one that isn't worth sharing.

On top of either form, `-Pstonecutter.versions=1.21.1,26.1` on the command line narrows
the resolved list down to that subset for a single run -- no TOML edit needed to build
just one version while testing.

## Per-mod version overrides

In `common.stonecutter.properties.toml`:

```toml
[overrides.tessera."1.21.1"]
neo_version = "21.1.180"
neo_version_range = "[21.1,21.2)"
```

Only `neo_version`/`neo_version_range` are overridden for `tessera` on `1.21.1`;
everything else (`minecraft_version`, `parchment_mappings_version`, etc.) is still
inherited from the base `["1.21.1"]` block.

## Optional JVM modules

```toml
[jvm.modules.ffm]
enable_preview = true
add_modules = "jdk.incubator.foreign"
add_to_runtime = true
```

`add_to_runtime` from the TOML can be overridden for a single run without editing
anything:

```
./gradlew runClient -Ppanzer.jvmModule.ffm.enabled=false
```

Useful to force a compatibility-mode run, or to turn on a module the TOML has
disabled, without switching JDKs or committing a TOML change.

## External tool versioning

```toml
[tools.oxipng]
version = "10.1.1"
enabled = true
```

Adding a new tool to `OptimizeTexturesTask` or a future task means declaring
`[tools.<name>]` here and calling
`ExternalToolResolver.resolve(spec, exeName) { target, ext, version -> url }` -- the
cache/download/extract cycle doesn't need to be rewritten per tool.

`OptimizeTexturesTask` writes a receipt file after a successful (or intentionally
skipped) run, so Gradle can mark it UP-TO-DATE and skip re-running oxipng when the
PNGs haven't changed.

## Early diagnostics

`settings.gradle.kts` runs validation right after merging the TOML, before any Gradle
project exists. It reports **every** problem found in one shot (missing keys,
malformed Maven ranges, overrides pointing at a version with no base block, JVM
modules declared without `src/<name>/java`, tools missing `version`) instead of
failing one at a time across repeated build attempts.

## A note on the generated `stonecutter.properties.toml`

`settings.gradle.kts` only rewrites this file when its content actually changed, so an
unrelated Gradle invocation doesn't bump its mtime and defeat any task that declares it
as an input. If a build fails right after this file is (re)generated and an immediate
retry with no changes succeeds, that's almost always a transient OS-level file lock --
most commonly antivirus scanning a freshly written file on Windows -- not an ordering
bug: Gradle's own phase model already guarantees the merge fully completes (in
Initialization) before any project configures (using `ModBuildProperties`, which reads
this file) or any task like `compileJava` runs (Execution). `TomlBlockReader.parse`
retries with backoff on `IOException` specifically to absorb that window.

## Wrapper and gradle.properties aren't shared the way everything else is

`includeBuild` (or the published-plugin fallback) resolves plugins and dependencies for
the main build, but the wrapper (`gradlew`, `gradlew.bat`, `gradle-wrapper.jar`) is what
starts Gradle before any build exists -- it can't live in another repo. `sync-mod.sh`
copies the wrapper, `gradle.properties`, and `settings.gradle.kts` into each mod on
request; there's no automatic propagation beyond that.
