# Panzer Build Logic

Everything PanzerDevOrg's NeoForge and Fabric mods share, in one repository: the Gradle build
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
    ├── panzer.mod-common.gradle.kts             # loader-independent part (toolchains, JVM modules, packaging, natives)
    ├── panzer.neoforge-mod.gradle.kts           # NeoForge nodes (ModDevGradle)
    ├── panzer.fabric-mod.gradle.kts             # Fabric nodes (Loom; remapping Loom for 1.21.x)
    ├── panzer.neoforge-mod-example.gradle.kts   # convention plugin for :example branches
    └── com/panzer/gradle/
        ├── PanzerSettingsPlugin.kt       # panzer.settings: TOML merge, versions, Stonecutter tree
        ├── PanzerModPlugin.kt            # panzer.mod: neoforge-mod or fabric-mod by node name
        ├── PanzerStonecutterPlugin.kt    # panzer.stonecutter: shared Stonecutter parameters, `fabric`/`neoforge` constants
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
| `panzer token-check` | checks `PANZER_SYNC_TOKEN` against every mod: valid, expiry, repositories visible, fetch and push allowed (changes nothing) |
| `panzer versions [list\|use\|all\|prefetch\|status\|clean]` | the Minecraft version matrix, what each mod builds and what is downloaded; work on some versions only; download every version once for all mods (see [Minecraft versions](#minecraft-versions)) |

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
Workflows read/write on the mod repositories; `.env.example` has the exact steps);
without it the workflow only reports. *Actions → Mods → action: token* checks the
secret from GitHub itself.
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

The mod's `build.gradle.kts` applies `panzer.mod` (or `panzer.neoforge-mod` for a
NeoForge-only mod) and keeps only what is
really its own (runs, extra tasks, publications). The plugin provides the rest:
NeoForge/Parchment setup, Java toolchains, JVM modules, `neoforge.mods.toml` and mixin
expansion (`mod_<key>` for every `[mod]` key, `mod_license`, version ranges,
`<dep>_version_range`), the legal files, natives, per-system jars and `buildAndCollect`.

### Fabric

`[stonecutter] loaders = ["neoforge", "fabric"]` gives every Minecraft version a second
Stonecutter node, `<version>-fabric`, built by `panzer.fabric-mod` with Fabric Loom
(Mojang names; the remapping Loom turns 1.21.x jars into intermediary in `remapJar`,
26.x is unobfuscated). Sources pick a loader with the `fabric`/`neoforge` constants
(`//? if fabric {`, `//? if fabric && >=26.1 {`), `fabric.mod.json` is expanded like
`neoforge.mods.toml` (`${fabric_minecraft_version_range}`, `${<dep>_fabric_version_range}`)
and each loader's jar leaves out the other's metadata. Jars are named
`<id>-<version>+<mc>-fabric.jar`; the publisher uploads them as separate Modrinth and
CurseForge files with the Fabric loader tag and a required Fabric API dependency.
`[fabric] loader_version` and each version's `fabric_api_version` live in the common TOML
(`panzer versions fabric` lists the newest); `-Ppanzer.loaders=fabric` builds one loader
only. Loom needs Gradle to run on Java 25, which CI already uses.

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
| build | `./gradlew build buildAndCollect -Ppanzer.native.strict=true` (`[ci] gradle_tasks` overrides; `[ci] display = true` runs it under Xvfb with Mesa, for tasks that start a game client), then `panzer ci verify-jars`: natives per jar, legal files, sources |
| maven | `[publish] maven_pages = true`, on tags: the mod's Maven repository on gh-pages |
| release | `mod-release.yml`: dry run with a `release-preview` artifact on every push, publishing on `v*` tags |

The build and maven jobs cache `~/.gradle/caches/modules-2`, `neoformruntime` and the
Gradle distribution, keyed by the versions built, their NeoForge builds, `[plugins]` and
the wrapper: Minecraft and NeoForge are downloaded and decompiled once per key, not on
every run.

`.github/workflows/mods.yml` runs the same for any mod from this repository's Actions
tab (*action: build*, never publishing), syncs or checks the shared files, or tags
releases (*action: release*). Its *versions* input builds only those versions, excluded
ones included (`1.21.11,26.1`): that is how a port is tried in CI before the mod claims
the version; such a run never publishes.

## Minecraft versions

Every mod builds from one version matrix, in `common.stonecutter.properties.toml`:
the Minecraft and NeoForge builds (and Parchment) for each version, and the `panzer`
profile listing them. All mods compile against the same builds, so each version is
downloaded and decompiled once per machine (and per CI cache key), whatever the
number of mods.

| Version | NeoForge | Java | Celeris claims |
|---|---|---|---|
| `1.21.1` | 21.1 | 21 | 1.21 - 1.21.6 |
| `1.21.10` | 21.10 | 21 | 1.21.7 - 1.21.10 |
| `1.21.11` | 21.11 | 21 | 1.21.11 |
| `26.1` | 26.1 | 25 | 26.1 - 26.3 |

Each mod claims its own ranges: what its code was verified against on that build.

A mod picks the profile and says what it claims for each version in its own
`["<version>"]` block, which merges into the common one key by key:

```toml
[stonecutter]
profile = "panzer"
exclude_versions = ["26.1"]          # not ported yet: not built by default
vcs_version = "1.21.1"               # the version the committed sources are written for

["1.21.10"]
minecraft_version_range = "[1.21.7,1.21.11)"
neo_version_range = "[21.7,21.11)"
game_versions = ["1.21.7", "1.21.8", "1.21.9", "1.21.10"]   # what the jar is published for
```

`versions = [...]` (an explicit list) always wins over a profile, and
`extra_versions` adds versions on top of it. Any common key (`neo_version`, ...) can be
overridden in the mod's block, at the cost of its own download.

### Working on a few versions

| | |
|---|---|
| `./gradlew build -Pstonecutter.versions=1.21.10` | one run, may name an excluded version |
| `panzer versions use 1.21.10 [--mod velox]` | this checkout: switches Stonecutter to 1.21.10 and writes `.panzer/versions` (git-ignored); Gradle and the IDE then configure, download and build only that |
| `panzer versions all` | every version again, active version back to `vcs_version` |
| `panzer versions` | the matrix: per mod `builds`, `excluded`, `local`/`skipped`, and whether NeoForge is downloaded |
| `panzer versions prefetch [26.1]` | downloads and decompiles each version once (through the first mod that builds it) |
| `panzer versions status` / `clean [--yes]` | cache sizes; removes NeoForge builds the matrix no longer uses |
| `panzer versions neoforge [1.21]` | NeoForge builds published per Minecraft version (newest, newest stable), next to the matrix's; to add or bump a version |
| `panzer versions fabric` | newest Fabric Loader, Loom and Fabric API per Minecraft version, next to the common TOML's |

The version Stonecutter has active is always configured, even outside the subset.
CI ignores `.panzer/versions`.

### Claiming more versions: `panzer compat`

One jar per build version may serve several Minecraft versions (`game_versions`
in the mod's `["<version>"]` block, e.g. the 1.21.1 build for 1.21 - 1.21.6).
`panzer compat` checks such a claim on the real thing: for every claimed version
other than the built one it installs that version's NeoForge dedicated server
(newest stable build, see `panzer versions neoforge`), puts the release jar and
its dependencies' jars in `mods/` (their minecraft/neoforge ranges widened, since
that is what is being checked) and runs the mod's own check:

```toml
[compat]
jvm_args = ["-Dvelox.parity=true"]   # makes the mod run its check and stop the server
report = "velox-parity.txt"          # in the server directory; first line ends with PASS
timeout_minutes = 15
server_properties = ["level-type=minecraft\\:flat", "online-mode=false"]
```

Without `report` the run is a smoke test (the server starts with the jar, then
stops cleanly): for libraries with no check of their own, an empty `[compat]`
table is enough.

A client-only mod sets `side = "client"`: each version then runs in a real game
client (a throwaway ModDevGradle project on that NeoForge, the jars in its
`run/mods`, Xvfb + Mesa in CI). Its check must write `report` in the game
directory and close the game; `client_options` lines go to `options.txt`.

```toml
[compat]
side = "client"
jvm_args = ["-Dtessera.selftest=true", "-Xmx2G"]
report = "tessera-selftest.txt"
client_options = ["onboardAccessibility:false", "narrator:0"]
```

In CI: Mods workflow, action `compat` (optionally `versions` = build versions to
check), one job per build version, a summary per claimed version and the server
logs as artifacts. Locally: `panzer compat plan --mod <dir>`, then
`panzer compat run --mod <dir> --build '<one entry of the plan>'` after
`./gradlew buildAndCollect`. Claim (`game_versions`, `minecraft_version_range`)
only what passes.

### Can players join? `panzer_join.py`

A mod with a network channel decides who can connect: NeoForge refuses a
connection when one side has a *required* channel the other lacks, and refuses
vanilla peers outright. The **Join test** workflow (manual) builds Celeris (any
ref), Velox and Tessera for each build version, starts a NeoForge dedicated
server and joins it with a real game client through Quick Play, under Xvfb:
a Velox server with a vanilla client and with a client without mods, a server
without mods and Mojang's vanilla server with a Tessera client, both together, plus controls with Celeris 0.2.0 (required
channel) that must be refused. With Fabric builds it also joins a Fabric server
(Fabric server launcher + Fabric API) with a Fabric client, a Velox (NeoForge)
server with a Fabric client, and a Fabric server with a vanilla client, each
checking that the log says "Celeris engine working". `tools/panzer_join.py --scenarios <json>` runs any
other combination of server and client jars.

## Per-mod version overrides

In `common.stonecutter.properties.toml`:

```toml
[overrides.tessera."1.21.1"]
neo_version = "21.1.180"
neo_version_range = "[21.1,21.2)"
```

Only those fields are overridden for `tessera` on `1.21.1`; everything else is
inherited from the base `["1.21.1"]` block. Setting the key in the mod's own
`["1.21.1"]` block does the same.

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
