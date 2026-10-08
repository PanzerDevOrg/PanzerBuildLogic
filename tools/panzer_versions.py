"""`panzer versions`: which Minecraft versions each mod builds, and the local
download cache they share.

Every mod takes its versions from the common TOML's profile, with the same
Minecraft and NeoForge builds, so each version is downloaded and decompiled
once per machine (in the Gradle user home) whatever the number of mods. On top
of that, a checkout can work on a few versions only (`use`): Gradle then
registers just those, so neither the build nor an IDE sync touches the others.
"""
from __future__ import annotations

import dataclasses
import os
import re
import shutil
import subprocess
from pathlib import Path

from panzer_mod import BUILD_LOGIC, COMMON_TOML, Mod, PanzerError, java_for, read_toml

LOCAL_FILE = Path(".panzer") / "versions"


def gradle_home() -> Path:
    return Path(os.environ.get("GRADLE_USER_HOME") or Path.home() / ".gradle")


def matrix(build_logic: Path = BUILD_LOGIC) -> list[dict]:
    """The common TOML's version blocks, in profile order."""
    common = read_toml(build_logic / COMMON_TOML)
    versions = []
    for profile in common.get("stonecutter", {}).get("profiles", {}).values():
        versions += [v for v in profile.get("versions", []) if v not in versions]
    rows = []
    for v in versions:
        block = common.get(v, {})
        mc = block.get("minecraft_version", v)
        rows.append({"version": v, "minecraft": mc, "neoforge": block.get("neo_version", "?"),
                     "java": java_for(mc)})
    return rows


def neoforge_cached(neoforge: str) -> bool:
    return (gradle_home() / "caches" / "modules-2" / "files-2.1" / "net.neoforged" / "neoforge" / neoforge).is_dir()


def cached_neoforge_versions() -> list[str]:
    root = gradle_home() / "caches" / "modules-2" / "files-2.1" / "net.neoforged" / "neoforge"
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


# --------------------------------------------------------------------------- per mod


def declared(mod: Mod) -> list[str]:
    return mod.stonecutter_versions


def local_subset(mod: Mod) -> list[str] | None:
    path = mod.root / LOCAL_FILE
    if not path.is_file():
        return None
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        values += [v.strip() for v in line.split("#")[0].split(",") if v.strip()]
    return values or None


def active(mod: Mod) -> str | None:
    script = mod.root / "stonecutter.gradle.kts"
    if not script.is_file():
        return None
    m = re.search(r'^stonecutter active "([^"]+)"', script.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def gradlew(mod: Mod, *tasks: str) -> None:
    script = mod.root / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if not script.is_file():
        raise PanzerError(f"{mod.name}: no Gradle wrapper (run `panzer sync` first)")
    command = [str(script), *tasks, "--console=plain"]
    if os.name != "nt":
        command.insert(0, "sh")
    print(f"{mod.name}: {' '.join(['gradlew', *tasks])}")
    result = subprocess.run(command, cwd=mod.root)
    if result.returncode != 0:
        raise PanzerError(f"{mod.name}: Gradle failed ({' '.join(tasks)})")


def use(mod: Mod, versions: list[str], switch: bool = True) -> str:
    unknown = [v for v in versions if v not in mod.available_versions]
    if unknown:
        raise PanzerError(f"{mod.name} cannot build {unknown}; it can build {mod.available_versions}")
    first = versions[0]
    if switch and active(mod) != first:
        # Stonecutter rewrites the sources for the new active version; it needs
        # every version registered for that, so the subset is written after.
        (mod.root / LOCAL_FILE).unlink(missing_ok=True)
        gradlew(mod, f"-Pstonecutter.versions={','.join(versions)}", f"Set active project to {first}")
    (mod.root / LOCAL_FILE).parent.mkdir(exist_ok=True)
    (mod.root / LOCAL_FILE).write_text(",".join(versions) + "\n", encoding="utf-8")
    return f"{mod.name}: works on {', '.join(versions)} (active {active(mod)}); `panzer versions all` restores every version"


def use_all(mod: Mod, reset: bool = True) -> str:
    (mod.root / LOCAL_FILE).unlink(missing_ok=True)
    vcs = mod.config.get("stonecutter", {}).get("vcs_version")
    if reset and vcs and active(mod) != vcs:
        gradlew(mod, "Reset active project")
    return f"{mod.name}: builds every version again ({', '.join(declared(mod))}), active {active(mod)}"


def prefetch(mods: list[Mod], versions: list[str] | None) -> list[str]:
    """Downloads and decompiles each version once, through the first mod that
    can build it; the other mods then find it in the shared Gradle cache."""
    wanted = versions or [row["version"] for row in matrix()]
    done, report = set(), []
    for mod in mods:
        mine = [v for v in wanted if v in mod.available_versions and v not in done]
        if not mine:
            continue
        gradlew(mod, f"-Pstonecutter.versions={','.join(mine)}",
                *[f":{v}:createMinecraftArtifacts" for v in mine])
        done.update(mine)
        report.append(f"{mod.name}: prepared {', '.join(mine)}")
    missing = [v for v in wanted if v not in done]
    if missing:
        report.append(f"no mod builds {', '.join(missing)}")
    return report


def dir_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def stale_neoforge() -> list[str]:
    used = {row["neoforge"] for row in matrix()}
    return [v for v in cached_neoforge_versions() if v not in used]


def clean(stale: list[str], dry_run: bool) -> list[str]:
    root = gradle_home() / "caches" / "modules-2" / "files-2.1" / "net.neoforged" / "neoforge"
    report = []
    for v in stale:
        path = root / v
        report.append(f"{'would remove' if dry_run else 'removed'} NeoForge {v} ({human(dir_size(path))})")
        if not dry_run:
            shutil.rmtree(path, ignore_errors=True)
    return report


@dataclasses.dataclass
class Row:
    version: str
    minecraft: str
    neoforge: str
    java: int
    cached: bool
    mods: dict[str, str]  # mod name -> "builds" / "excluded" / "local" / "-"


def table(mods: list[Mod]) -> list[Row]:
    rows = []
    for m in matrix():
        states = {}
        for mod in mods:
            subset = local_subset(mod)
            v = m["version"]
            if v not in mod.available_versions:
                state = "-"
            elif subset is not None:
                state = "local" if v in subset or v == active(mod) else "skipped"
            else:
                state = "builds" if v in declared(mod) else "excluded"
            states[mod.name] = state
        rows.append(Row(m["version"], m["minecraft"], m["neoforge"], m["java"], neoforge_cached(m["neoforge"]), states))
    return rows


# --------------------------------------------------------------------------- NeoForge releases

NEOFORGE_METADATA = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"


def minecraft_of(neoforge: str) -> str:
    """NeoForge's numbering: 21.4.x is Minecraft 1.21.4 (21.0.x: 1.21), 26.1.0.x is 26.1, 26.1.1.x is 26.1.1."""
    nums = [int(n) for n in re.findall(r"\d+", neoforge.split("-")[0])] + [0, 0]
    if nums[0] >= 26:
        return f"{nums[0]}.{nums[1]}" + (f".{nums[2]}" if nums[2] else "")
    return f"1.{nums[0]}" + (f".{nums[1]}" if nums[1] else "")


def _order(version: str) -> tuple:
    base, _, suffix = version.partition("-")
    return tuple(int(n) for n in re.findall(r"\d+", base)), suffix == "", suffix


def _mc_order(minecraft: str) -> tuple:
    return tuple(int(n) for n in minecraft.split("."))


@dataclasses.dataclass
class Release:
    minecraft: str
    latest: str
    stable: str | None
    count: int


def neoforge_releases(metadata_xml: str) -> list[Release]:
    """Per Minecraft version (oldest first): the newest NeoForge build and the newest non-beta one."""
    groups: dict[str, list[str]] = {}
    for version in re.findall(r"<version>([^<]+)</version>", metadata_xml):
        groups.setdefault(minecraft_of(version), []).append(version)
    releases = []
    for mc in sorted(groups, key=_mc_order):
        versions = sorted(groups[mc], key=_order)
        stable = [v for v in versions if "-" not in v]
        releases.append(Release(mc, versions[-1], stable[-1] if stable else None, len(versions)))
    return releases


def fetch_neoforge_metadata(url: str = NEOFORGE_METADATA) -> str:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.read().decode("utf-8")
    except OSError as e:
        raise PanzerError(f"cannot read {url}: {e}") from e


def neoforge_table(since: str | None = None, metadata_xml: str | None = None) -> list[str]:
    """`panzer versions neoforge`: one line per Minecraft version from `since` (default 1.21) on."""
    in_matrix = {row["minecraft"]: row["neoforge"] for row in matrix()}
    floor = _mc_order(since or "1.21")
    lines = [f"{'minecraft':10} {'builds':>6}  {'newest':22} {'newest stable':16} matrix"]
    for r in neoforge_releases(metadata_xml if metadata_xml is not None else fetch_neoforge_metadata()):
        if _mc_order(r.minecraft) < floor:
            continue
        used = in_matrix.get(r.minecraft)
        note = "" if used is None else used + ("" if used in (r.latest, r.stable) else " (newer available)")
        lines.append(f"{r.minecraft:10} {r.count:>6}  {r.latest:22} {r.stable or '-':16} {note}".rstrip())
    return lines


# --------------------------------------------------------------------------- Fabric releases

FABRIC_API_METADATA = "https://maven.fabricmc.net/net/fabricmc/fabric-api/fabric-api/maven-metadata.xml"
FABRIC_LOADER_META = "https://meta.fabricmc.net/v2/versions/loader"
FABRIC_LOOM_METADATA = "https://maven.fabricmc.net/net/fabricmc/fabric-loom/maven-metadata.xml"


def fabric_api_releases(metadata_xml: str) -> dict[str, list[str]]:
    """Fabric API builds per Minecraft version (oldest build first): versions read
    `<api>+<minecraft>`, e.g. 0.116.7+1.21.1."""
    groups: dict[str, list[str]] = {}
    for version in re.findall(r"<version>([^<]+)</version>", metadata_xml):
        api, _, minecraft = version.partition("+")
        if minecraft and re.fullmatch(r"\d+(\.\d+)+", minecraft):
            groups.setdefault(minecraft, []).append(version)
    return {mc: sorted(v, key=lambda x: _order(x.partition("+")[0])) for mc, v in groups.items()}


def _fetch(url: str) -> str:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.read().decode("utf-8")
    except OSError as e:
        raise PanzerError(f"cannot read {url}: {e}") from e


def fabric_table(since: str | None = None, api_xml: str | None = None, loader_json: str | None = None,
                 loom_xml: str | None = None) -> list[str]:
    """`panzer versions fabric`: newest Fabric API per Minecraft version from `since`
    (default 1.21) on, plus the newest stable Fabric Loader and Loom."""
    import json as _json
    floor = _mc_order(since or "1.21")
    api = fabric_api_releases(api_xml if api_xml is not None else _fetch(FABRIC_API_METADATA))
    loaders = _json.loads(loader_json if loader_json is not None else _fetch(FABRIC_LOADER_META))
    stable_loader = next((l["version"] for l in loaders if l.get("stable")), loaders[0]["version"] if loaders else "-")
    looms = [v for v in re.findall(r"<version>([^<]+)</version>",
                                   loom_xml if loom_xml is not None else _fetch(FABRIC_LOOM_METADATA))
             if "SNAPSHOT" not in v]
    loom = sorted(looms, key=_order)[-1] if looms else "-"
    lines = [f"fabric loader (newest stable): {stable_loader}", f"fabric loom (newest): {loom}",
             f"{'minecraft':10} {'builds':>6}  fabric api (newest)"]
    for mc in sorted(api, key=_mc_order):
        if _mc_order(mc) < floor:
            continue
        lines.append(f"{mc:10} {len(api[mc]):>6}  {api[mc][-1]}")
    return lines
