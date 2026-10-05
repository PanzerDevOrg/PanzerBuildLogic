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

from panzer_mod import BUILD_LOGIC, COMMON_TOML, Mod, PanzerError, read_toml

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
                     "java": 25 if int(re.findall(r"\d+", mc)[0]) >= 26 else 21})
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
