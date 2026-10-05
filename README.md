# Panzer Build Logic

Everything PanzerDevOrg's NeoForge mods share, in one repository: the Gradle build
(Stonecutter multi-version setup, natives, packaging), the licenses and legal notices,
the CI pipeline, the release publisher, and the files every mod repository carries
(Gradle wrapper, settings, workflows). Change something here once; `panzer sync` (or
CI, automatically) puts it into every mod.

## Structure

```
panzer-build-logic/
├── panzer, panzer.cmd                    # the `panzer` tool (tools/panzer.py), Linux/macOS and Windows
├── .env.example                          # tokens and local settings for `panzer` (copy to .env)
├── mods.toml                             # every mod built on this repository
├── common.stonecutter.properties.toml    # shared TOML: [legal], [plugins], [dependencies],
│                                         # [neoforge], [tools.*], ["1.21.x"] blocks,
│                                         # [overrides.*], [stonecutter.profiles.*]
├── legal/
│   ├── catalog.toml                      # licenses and third-party components
│   ├── licenses/                         # full license texts (AGPL-3.0, CC BY-NC-SA 4.0)
│   └── third-party/                      # upstream license files (zstd, bc7enc_rdo)
├── shared/
│   ├── manifest.toml                     # what `panzer sync` keeps identical in every mod
│   ├── mod/                              # those files (settings, workflows, ...)
│   └── new-mod/                          # template for `panzer new`
├── tools/                                # panzer: sync, check, new, release, secrets, ci
├── publishing/                           # release + description publisher (Modrinth, CurseForge, GitHub)
├── .github/workflows/
│   ├── mod-ci.yml                        # every mod's CI (reusable): natives, build, verify, release
│   ├── mod-release.yml                   # GitHub release, Modrinth, CurseForge (reusable)
│   ├── mod-description.yml               # README -> Modrinth / CurseForge descriptions (reusable)
│   ├── mods.yml                          # control center: sync / check / build / release every mod
│   └── publish.yml                       # this repository's own CI and plugin publishing
└── src/main/kotlin/
    ├── panzer.neoforge-mod.gradle.kts           # main convention plugin
    ├── panzer.neoforge-mod-example.gradle.kts   # convention plugin for :example branches
    └── com/panzer/gradle/
        ├── PanzerSettingsPlugin.kt       # panzer.settings: TOML merge, versions, Stonecutter tree
        ├── PanzerStonecutterPlugin.kt    # panzer.stonecutter: shared Stonecutter parameters
        ├── ModPackaging.kt               # mods.toml expansion, legal files, natives, buildAndCollect, [depends_on]
        ├── PlatformJars.kt               # per-system jars ([publish] platforms)
        ├── NativeLibraries.kt            # [natives.<name>] specs; CMakeBuildTask.kt builds them
        ├── TomlMerge.kt, TomlBlockReader.kt, ModBuildProperties.kt, VersionOverrides.kt
        ├── PanzerDiagnostics.kt          # early validation of the merged TOML
        ├── ToolVersions.kt, ExternalToolResolver.kt, OptimizeTexturesTask.kt
        └── JvmModulePreprocessor.kt, PreprocessJvmModuleTask.kt, NeoForgeMutexPlugin.kt
```

## The `panzer` tool

Python 3.11+ only (no packages, except `markdown-it-py` for `panzer publish`). Run it
from anywhere: `./panzer` on Linux/macOS, `panzer` (panzer.cmd) on Windows.

| Command | Does |
|---|---|
| `panzer mods` | lists the mods in `mods.toml`, their local checkout and whether they are in sync |
| `panzer sync [MOD...] [--all]` | writes the shared files into mod checkouts (`--diff` shows the changes) |
| `panzer check [MOD...] [--all]` | the same as a report; exits 1 if anything differs |
| `panzer new ../Shiny --id shiny` | scaffolds a new mod (TOML, build script, main class, README, changelog) and syncs it |
| `panzer release celeris --push` | checks the changelog, a clean tree and the shared files, then pushes `v<version>`; the tag's CI publishes |
| `panzer secrets [--org PanzerDevOrg]` | copies `MODRINTH_TOKEN`, `CURSEFORGE_TOKEN`, `PANZER_SYNC_TOKEN` from `.env` into GitHub Actions secrets (needs `gh`) |
| `panzer publish ...` | the release publisher, see [`publishing/README.md`](publishing/README.md) |
| `panzer doctor` | checks Python, git/gh/java, tokens and mod checkouts |

`MOD` is a path or a key of `mods.toml`. Mod checkouts are looked up next to this
repository (`../Celeris`, `../Tessera`, `../velox`), or under `PANZER_MODS_DIR`.

### `.env`

Copy `.env.example` to `.env` (git-ignored) and fill in what you use: the Modrinth and
CurseForge tokens for local publishing, `PANZER_SYNC_TOKEN` for the Mods workflow,
`PANZER_MODS_DIR`. `panzer` and the publisher read `.env` from the current directory,
the mod and this repository; real environment variables always win. `panzer secrets`
then sets the same tokens on GitHub, so they live in one file.

## Shared files

`shared/manifest.toml` lists what every mod repository carries and nobody should edit
per mod:

| In each mod | Comes from |
|---|---|
| `gradlew`, `gradlew.bat`, `gradle/wrapper/*` | this repository's own wrapper |
| `settings.gradle.kts`, `stonecutter.gradle.kts`, `gradle.properties`, `.gitattributes` | `shared/mod/` (the `stonecutter active` line stays the mod's) |
| `.github/workflows/ci.yml`, `descriptions.yml` | `shared/mod/.github/workflows/` (thin callers of the reusable workflows) |
| `.gitignore` | the block between the `panzer-build-logic` markers; the rest is the mod's |
| `LICENSE`, `LICENSE-AGPL`, `LICENSE-CC`, `NOTICE` | `legal/` and `[legal]` (below) |
| README `<!-- panzer:license -->` and `<!-- panzer:footer -->` blocks | `legal/` and `[legal]` |

Former per-mod copies (`package.yml`, `src/main/resources/LICENSE`, ...) are deleted by
the sync.

**Automatic:** a push here that changes `shared/`, `legal/`, `tools/`, the wrapper, the
common TOML or `mods.toml` runs `.github/workflows/mods.yml`, which syncs every mod and
commits the result straight to its `master` (author `bichal`, overridable with the
`PANZER_GIT_NAME` / `PANZER_GIT_EMAIL` repository variables). That push runs the mod's
own CI. It needs the `PANZER_SYNC_TOKEN` secret (fine-grained token, Contents and
Workflows read/write on the mod repositories); without it the workflow only reports.
Each mod's CI also warns when its shared files are out of date.

## Legal

`legal/catalog.toml` holds the licenses (SPDX id, full text, file name, one-line
summary) and the third-party components mods bundle (upstream license file, copyright,
what ends up in the jar). The common TOML says which licenses every mod uses:

```toml
[legal]
holder = "Panzer"
code = "AGPL-3.0-only"
assets = "CC-BY-NC-SA-4.0"
```

A mod lists what it bundles, and can replace `[legal]` with its own complete table:

```toml
[legal.third_party]
components = ["zstd"]
```

From that, `panzer sync` writes `LICENSE` (summary), the full text of each license,
`NOTICE` (every component's notice and license text, which BSD and MIT require for
binary redistribution) and the README's license table and footer. The build copies
`LICENSE*` and `NOTICE` into `META-INF/` of every jar and sources jar, and uses `code`
as `neoforge.mods.toml`'s `license`. A new license or component is one catalog entry.

## How a mod consumes this repository

Clone `panzer-build-logic` **next to** each mod. A mod's `settings.gradle.kts` (shared,
a few lines) `includeBuild`s it and applies `panzer.settings`, which:

1. merges `common.stonecutter.properties.toml` with the mod's
   `mod.stonecutter.properties.toml` into `stonecutter.properties.toml` (generated,
   git-ignored; a table the mod declares replaces the common one);
2. applies Stonecutter and the Foojay toolchain resolver, at the versions in `[plugins]`;
3. resolves the Minecraft versions (`[stonecutter]`, below) and creates the Stonecutter
   tree for the root project plus `[stonecutter] branches = ["example"]`;
4. validates the merged TOML and reports every problem at once.

The mod's `build.gradle.kts` applies `panzer.neoforge-mod` and keeps only what is
really its own (runs, extra tasks, publications). The plugin provides the rest:
NeoForge/Parchment setup, Java toolchains, JVM modules, `neoforge.mods.toml` and mixin
expansion (`mod_<key>` for every `[mod]` key, `mod_license`, version ranges,
`<dep>_version_range`), the legal files, natives, per-system jars and `buildAndCollect`.

### Dependencies on other mods

```toml
[depends_on.celeris]
version = "0.2.0"                                 # compiled against
version_range = "[0.2.0,)"                        # neoforge.mods.toml: ${celeris_version_range}
artifact = "com.panzer.mods:celeris-{mc}"         # {mc}: the Minecraft version being built
maven = "https://panzerdevorg.github.io/Celeris/maven"
repo = "PanzerDevOrg/Celeris"
ci_build_from_source = true                       # CI publishes its master to mavenLocal first
```

Resolved from `mavenLocal()` first (a local `publishToMavenLocal` wins), then `maven`;
`-Ppanzer.celeris.maven=<url>` points at another repository.

## CI

Each mod's `.github/workflows/ci.yml` calls `mod-ci.yml`, which reads the mod's TOML
(`panzer ci plan`) and runs:

| Job | Does |
|---|---|
| plan | native matrix, JDKs (21, plus 25 for 26.x), source dependencies, publish or dry run, shared-file check |
| natives | each CMake `[natives.<name>]` platform on its own runner, with its CTest suite; `ci_prepare` runs first, `ci_linux_script` builds all Linux platforms in one job instead |
| build | `./gradlew build buildAndCollect -Ppanzer.native.strict=true` (`[ci] gradle_tasks` overrides), then `panzer ci verify-jars`: natives per jar, legal files, sources |
| maven | `[publish] maven_pages = true`, on tags: the mod's Maven repository on gh-pages |
| release | `mod-release.yml`: dry run with a `release-preview` artifact on every push, publishing on `v*` tags |

`.github/workflows/mods.yml` runs the same for any mod from this repository's Actions
tab (*action: build*, never publishing), syncs or checks the shared files, or tags
releases (*action: release*).

## `[stonecutter]`: choosing which Minecraft versions to build

```toml
[stonecutter]
versions = ["1.21.1", "1.21.4"]   # explicit list -- always wins if present
vcs_version = "1.21.1"
branches = ["example"]            # optional: extra projects built for every version
```

```toml
[stonecutter]
profile = "legacy"                 # named list from [stonecutter.profiles.legacy]
extra_versions = ["26.1"]          # optional: add versions on top of the profile
exclude_versions = ["1.21.4"]      # optional: remove versions from the profile
```

Profiles are defined once in `common.stonecutter.properties.toml`. On top of either
form, `-Pstonecutter.versions=1.21.1,26.1` narrows the list for a single run.

## Per-mod version overrides

In `common.stonecutter.properties.toml`:

```toml
[overrides.tessera."1.21.1"]
neo_version = "21.1.180"
neo_version_range = "[21.1,21.2)"
```

Only those fields are overridden for `tessera` on `1.21.1`; everything else is
inherited from the base `["1.21.1"]` block.

## Optional JVM modules

```toml
[jvm.modules.ffm]
enable_preview = true
add_modules = "jdk.incubator.foreign"
add_to_runtime = true
```

`./gradlew runClient -Ppanzer.jvmModule.ffm.enabled=false` overrides `add_to_runtime`
for one run, e.g. to test the fallback path.

## Native libraries

```toml
[natives.celeris_physics]
cmake_dir = "native"            # CMake project; omit for prebuilt binaries
headers = "native/include"      # optional: zipped as the `native-headers` artifact
platforms = ["linux-x86_64", "linux-aarch64", "windows-x86_64", "macos-x86_64", "macos-aarch64"]
required = false                # optional: the mod runs without it
jar_name_linux = "libfoo.so.1"  # optional: name inside the jar, per OS
ci_prepare = "native/fetch.sh"  # optional (CI): run before building
ci_linux_script = "native/build-linux.sh"  # optional (CI): builds every Linux platform
```

Binaries live in `natives/<os>/<arch>/` (committed, or built); `collectNatives` stages
them into the jar as `natives/<os>-<arch>/`. `-Ppanzer.native.mode=fat|auto|off`,
`-Ppanzer.native.target=<os>-<arch>` and `-Ppanzer.native.strict=true` (CI: every
platform must be present) control it. Root-project tasks:

| Task | Does |
|---|---|
| `buildNative<Name>` | CMake configure + build + `ctest` for the host, copied to `natives/<os>/<arch>/` |
| `buildNatives` | every `buildNative<Name>` |
| `nativeHeaders<Name>` | zip of `headers`, classifier `native-headers` |

`-Ppanzer.native.build=true` rebuilds the host binary before packaging;
`-Ppanzer.native.buildType`, `-Ppanzer.native.cmakeArgs`, `-Ppanzer.native.test` tune it.

## Publishing (Modrinth, CurseForge, GitHub releases)

README.md is the single description for all sites; per-system jars go to GitHub
releases; CurseForge tags are resolved explicitly; every push gets a dry-run preview.
See [`publishing/README.md`](publishing/README.md).

## External tool versioning

```toml
[tools.oxipng]
version = "10.1.1"
enabled = true
```

A new tool declares `[tools.<name>]` and calls
`ExternalToolResolver.resolve(spec, exeName) { target, ext, version -> url }`.
`OptimizeTexturesTask` writes a receipt so it stays UP-TO-DATE when the PNGs haven't
changed.

## Releasing panzer-build-logic itself

Mods build against the sibling checkout, so a push to `master` is live for every mod's
next build (and the Mods workflow syncs the shared files). A `v*` tag additionally
publishes the plugin to `https://panzerdevorg.github.io/PanzerBuildLogic/maven`.

## A note on the generated `stonecutter.properties.toml`

`panzer.settings` rewrites it only when its content changes, so tasks reading it stay
UP-TO-DATE. If a build fails right after it was regenerated and an immediate retry
succeeds, that is a transient file lock (usually antivirus on Windows);
`TomlBlockReader.parse` retries with backoff for that reason.
