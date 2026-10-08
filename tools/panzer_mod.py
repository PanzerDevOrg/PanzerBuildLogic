"""Mod configuration and .env loading shared by every panzer command.

A mod is a directory with mod.stonecutter.properties.toml next to a
panzer-build-logic checkout. Its effective configuration is that TOML merged
over panzer-build-logic/common.stonecutter.properties.toml with the same rule
the Gradle build uses (TomlMerge.kt): a table the mod declares replaces the
common table of the same name; sub-tables are separate tables.
"""
from __future__ import annotations

import dataclasses
import os
import re
import tomllib
from pathlib import Path

BUILD_LOGIC = Path(__file__).resolve().parent.parent
MOD_TOML = "mod.stonecutter.properties.toml"
COMMON_TOML = "common.stonecutter.properties.toml"


class PanzerError(Exception):
    pass


# --------------------------------------------------------------------------- .env


def parse_dotenv(text: str) -> dict[str, str]:
    """KEY=VALUE lines; `export` prefixes, comments, blank lines and quotes allowed."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[key] = value
    return values


def load_dotenv(*directories: Path) -> list[Path]:
    """Loads `.env` from each directory (first wins); real environment variables
    always win over .env values. Returns the files that were read."""
    loaded = []
    for directory in directories:
        path = Path(directory) / ".env"
        if not path.is_file():
            continue
        for key, value in parse_dotenv(path.read_text(encoding="utf-8")).items():
            if value and not os.environ.get(key):
                os.environ[key] = value
        loaded.append(path)
    return loaded


# --------------------------------------------------------------------------- TOML


def read_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


VERSION_KEY = re.compile(r"^\d+(\.\d+)+(-[\w.]+)?$")


def merge_tables(common: dict, mod: dict, top: bool = True) -> dict:
    """Header-level merge, like TomlMerge.kt: if the mod's version of a table has
    any key of its own, those keys replace the common table's keys entirely;
    sub-tables are merged the same way, one by one. Minecraft version blocks
    (["1.21.1"], top level only) merge key by key instead: the common block's
    keys, with the mod's added or overriding."""
    def scalars(table: dict) -> dict:
        return {k: v for k, v in table.items() if not isinstance(v, dict)}

    merged = dict(scalars(mod) if scalars(mod) else scalars(common))
    for key in list(common) + [k for k in mod if k not in common]:
        c, m = common.get(key), mod.get(key)
        if top and VERSION_KEY.match(key) and isinstance(c, dict) and isinstance(m, dict):
            merged[key] = {**c, **m}
        elif isinstance(c, dict) or isinstance(m, dict):
            merged[key] = merge_tables(c if isinstance(c, dict) else {}, m if isinstance(m, dict) else {}, top=False)
    return merged


@dataclasses.dataclass
class Mod:
    root: Path
    toml: dict  # the mod's own TOML
    config: dict  # merged with the common TOML

    @property
    def id(self) -> str:
        return self.config["mod"]["id"]

    @property
    def name(self) -> str:
        return self.config["mod"].get("name", self.id)

    @property
    def version(self) -> str:
        return self.config["mod"]["version"]

    @property
    def legal(self) -> dict:
        return self.config.get("legal", {})

    @property
    def github_repo(self) -> str | None:
        """Owner/Repo from [mod] issues (https://github.com/Owner/Repo/issues)."""
        m = re.match(r"https://github\.com/([^/]+/[^/]+)", self.config["mod"].get("issues", ""))
        return m.group(1) if m else None

    @property
    def available_versions(self) -> list[str]:
        """Every version the mod could build: its explicit list, or its profile
        plus extra_versions, excluded ones included (for trying a port)."""
        sc = self.config.get("stonecutter", {})
        if "versions" in sc:
            return list(sc["versions"])
        profile = sc.get("profiles", {}).get(sc.get("profile", ""), {})
        return list(dict.fromkeys(list(profile.get("versions", [])) + list(sc.get("extra_versions", []))))

    @property
    def stonecutter_versions(self) -> list[str]:
        """The versions it builds by default (PanzerSettingsPlugin.resolveVersions)."""
        sc = self.config.get("stonecutter", {})
        if "versions" in sc:
            return list(sc["versions"])
        return [v for v in self.available_versions if v not in sc.get("exclude_versions", [])]

    @property
    def natives(self) -> dict[str, dict]:
        return self.config.get("natives", {})

    @property
    def depends_on(self) -> dict[str, dict]:
        return self.config.get("depends_on", {})


def load_mod(root: Path, build_logic: Path = BUILD_LOGIC) -> Mod:
    root = Path(root).resolve()
    mod_toml = root / MOD_TOML
    if not mod_toml.is_file():
        raise PanzerError(f"{mod_toml} not found: is {root} a mod repository?")
    toml = read_toml(mod_toml)
    common_path = build_logic / COMMON_TOML
    common = read_toml(common_path) if common_path.is_file() else {}
    mod = Mod(root, toml, merge_tables(common, toml))
    for key in ("id", "version"):
        if key not in mod.config.get("mod", {}):
            raise PanzerError(f"[mod] {key} is missing in {mod_toml}")
    return mod


def java_for(minecraft: str) -> int:
    """Same rule as ModBuildProperties.requiredJava."""
    parts = [int(p) for p in re.findall(r"\d+", minecraft)[:2]]
    return 25 if parts and parts[0] >= 26 else 21


def java_home(java: int) -> str:
    """The JDK for `java`: JAVA_HOME_<n>_X64 (setup-java's names), or JAVA_HOME for 21."""
    home = os.environ.get(f"JAVA_HOME_{java}_X64") or (os.environ.get("JAVA_HOME") if java == 21 else None)
    if not home:
        raise PanzerError(f"no Java {java} (set JAVA_HOME_{java}_X64)")
    return home


def java_env(java: int) -> dict[str, str]:
    """This process's environment with JAVA_HOME and PATH pointing at JDK `java`."""
    env = dict(os.environ, JAVA_HOME=java_home(java))
    env["PATH"] = f"{env['JAVA_HOME']}/bin{os.pathsep}{env['PATH']}"
    return env


# --------------------------------------------------------------------------- registry


@dataclasses.dataclass
class Registered:
    key: str
    repo: str  # Owner/Repo on GitHub
    path: Path | None  # local checkout, if any
    private: bool = False  # only readable with PANZER_SYNC_TOKEN in panzer-build-logic's CI


def registry(build_logic: Path = BUILD_LOGIC) -> list[Registered]:
    """Mods listed in panzer-build-logic/mods.toml, with their local checkout
    (PANZER_MODS_DIR, default: the folder holding panzer-build-logic)."""
    data = read_toml(build_logic / "mods.toml")
    base = Path(os.environ.get("PANZER_MODS_DIR") or build_logic.parent)
    mods = []
    for key, entry in data.get("mods", {}).items():
        folder = entry.get("folder", entry["repo"].split("/")[-1])
        candidates = [base / folder, base / folder.lower(), base / key]
        path = next((c for c in candidates if (c / MOD_TOML).is_file()), None)
        mods.append(Registered(key.lower(), entry["repo"], path, bool(entry.get("private", False))))
    return mods
