"""CI helpers for .github/workflows/mod-ci.yml: everything that differs between
mods comes out of their TOML here, so the workflow itself is the same for all.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from panzer_legal import Legal
from panzer_mod import Mod, PanzerError, java_for

# GitHub-hosted runner per native platform (ppc64le/riscv64 have none: such
# libraries must be prebuilt and committed under natives/).
RUNNERS = {
    "linux-x86_64": "ubuntu-latest",
    "linux-aarch64": "ubuntu-24.04-arm",
    "windows-x86_64": "windows-latest",
    "windows-aarch64": "windows-11-arm",
    "macos-x86_64": "macos-15-intel",
    "macos-aarch64": "macos-14",
}
OPERATING_SYSTEMS = ("windows", "linux", "macos")


def native_file(lib: str, platform: str) -> str:
    """CMake's default shared-library name, as NativeLibrarySpec.fileName."""
    os_name = platform.split("-", 1)[0]
    return {"windows": f"{lib}.dll", "macos": f"lib{lib}.dylib"}.get(os_name, f"lib{lib}.so")


def jar_entry(lib: str, spec: dict, platform: str) -> str:
    os_name = platform.split("-", 1)[0]
    return f"natives/{platform}/{spec.get(f'jar_name_{os_name}', native_file(lib, platform))}"


def native_matrix(mod: Mod) -> list[dict]:
    """One job per CMake-backed [natives.<name>] platform; ci_linux_script
    builds every Linux platform of that library in a single job instead."""
    entries = []
    for lib, spec in mod.natives.items():
        if not spec.get("cmake_dir"):
            continue
        platforms = list(spec.get("platforms", []))
        common = {"lib": lib, "prepare": spec.get("ci_prepare", ""), "cmake_dir": spec["cmake_dir"]}
        script = spec.get("ci_linux_script", "")
        if script:
            linux = [p for p in platforms if p.startswith("linux-")]
            platforms = [p for p in platforms if not p.startswith("linux-")]
            if linux:
                outputs = [f"natives/linux/{p.split('-', 1)[1]}/{native_file(lib, p)}" for p in linux]
                entries.append(dict(common, key="linux", platform=" ".join(linux), runner=RUNNERS["linux-x86_64"],
                                    script=script, file="", outputs=" ".join(outputs)))
        for p in platforms:
            if p not in RUNNERS:
                raise PanzerError(f"[natives.{lib}] {p}: no GitHub runner builds it; commit a prebuilt binary "
                                  f"under natives/ and drop cmake_dir, or remove the platform")
            os_name, arch = p.split("-", 1)
            entries.append(dict(common, key=p, platform=p, runner=RUNNERS[p], script="",
                                file=native_file(lib, p), outputs=f"natives/{os_name}/{arch}/{native_file(lib, p)}"))
    return entries


def plan(mod: Mod) -> dict:
    versions = mod.stonecutter_versions
    javas = sorted({java_for(mod.config.get(v, {}).get("minecraft_version", v)) for v in versions}, reverse=True)
    # setup-java makes the last one the default JAVA_HOME (Gradle's own JVM): 21.
    javas = [j for j in javas if j != 21] + [21]
    source_deps = [
        {"id": dep_id, "repo": dep["repo"], "versions": ",".join(versions)}
        for dep_id, dep in mod.depends_on.items()
        if dep.get("ci_build_from_source") and dep.get("repo")
    ]
    ci = mod.config.get("ci", {})
    natives = native_matrix(mod)
    return {
        "mod-id": mod.id,
        "version": mod.version,
        "natives": json.dumps({"include": natives}),
        "has-natives": str(bool(natives)).lower(),
        "strict-natives": str(bool(mod.natives)).lower(),
        "java": "\n".join(str(j) for j in javas),
        "source-deps": json.dumps(source_deps),
        "gradle-tasks": ci.get("gradle_tasks", "build buildAndCollect"),
        "jars-artifact": f"{mod.id}-jars",
        "maven-pages": str(bool(mod.config.get("publish", {}).get("maven_pages"))).lower(),
    }


# --------------------------------------------------------------------------- jar verification


def verify_jars(mod: Mod, libs: Path, strict: bool) -> tuple[list[str], list[str]]:
    """Checks every collected jar of a build (errors, report lines):
    the universal jar carries every declared native and the legal files, each
    per-system jar only its own OS's natives, the java jar none, and every
    sources jar exists with the legal files."""
    errors, report = [], []
    version_dir = libs / mod.version
    pattern = re.compile(rf"^{re.escape(mod.id)}-{re.escape(mod.version)}\+([^-]+(?:-[^-]+)*?)\.jar$")
    universal = sorted(p for p in version_dir.glob("*.jar")
                       if pattern.match(p.name) and not re.search(r"-(sources|dev|windows|linux|macos|java)\.jar$", p.name))
    if not universal:
        return [f"no {mod.id}-{mod.version}+<minecraft>.jar in {version_dir}"], report

    legal = Legal(mod)
    legal_entries = ["META-INF/LICENSE", f"META-INF/{legal.code['file']}", f"META-INF/{legal.assets['file']}"]
    if legal.components:
        legal_entries.append("META-INF/NOTICE")
    platforms = mod.config.get("publish", {}).get("platforms", [])
    native_oses = {p.split("-", 1)[0] for spec in mod.natives.values() for p in spec.get("platforms", [])}

    def natives_in(names: list[str]) -> set[str]:
        return {n.split("/")[1] for n in names if n.startswith("natives/") and n.count("/") >= 2}

    for jar in universal:
        base = jar.with_suffix("")
        names = zipfile.ZipFile(jar).namelist()
        for entry in legal_entries:
            if entry not in names:
                errors.append(f"{jar.name} lacks {entry}")
        for lib, spec in mod.natives.items():
            for p in spec.get("platforms", []):
                entry = jar_entry(lib, spec, p)
                if entry not in names and (strict or spec.get("required", True)):
                    errors.append(f"{jar.name} lacks {entry}")
        for platform in platforms:
            sibling = Path(f"{base}-{platform}.jar")
            if not sibling.is_file():
                errors.append(f"missing {sibling.name}")
                continue
            found = natives_in(zipfile.ZipFile(sibling).namelist())
            if platform == "java":
                if found:
                    errors.append(f"{sibling.name} contains natives: {sorted(found)}")
                continue
            foreign = sorted(c for c in found if not c.startswith(f"{platform}-"))
            if foreign:
                errors.append(f"{sibling.name} contains other systems' natives: {foreign}")
            if platform in native_oses and not found:
                errors.append(f"{sibling.name} has no natives")
        sources = Path(f"{base}-sources.jar")
        if not sources.is_file():
            errors.append(f"missing {sources.name}")
        elif "META-INF/LICENSE" not in zipfile.ZipFile(sources).namelist():
            errors.append(f"{sources.name} lacks META-INF/LICENSE")
        extra = "/".join(platforms + ["sources"])
        report.append(f"{jar.name}: {len(natives_in(names))} native platforms, legal files, + {extra}")
    return errors, report
