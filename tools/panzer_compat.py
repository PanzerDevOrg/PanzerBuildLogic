"""`panzer compat`: checks a mod's release jar on the other Minecraft versions it
claims (``game_versions`` in its ["<version>"] blocks), on a real NeoForge
dedicated server of each version.

One jar per build version serves several Minecraft versions (e.g. the 1.21.1
build for 1.21 - 1.21.6). Instead of trusting that, ``compat`` installs the
NeoForge server of every claimed version, puts the built jar (and its
dependencies' jars) in ``mods/`` and runs the mod's own check, configured in
its TOML:

    [compat]
    jvm_args = ["-Dvelox.parity=true"]   # what makes the mod run its check
    report = "velox-parity.txt"          # written in the server directory; first line ends with PASS
    timeout_minutes = 15
    server_properties = ["level-type=minecraft\\:flat", ...]

Without `report` (a library such as Celeris, with no check of its own) the run
is a smoke test: the server must start with the jar ("Done"), then stop cleanly.

The jars' own minecraft/neoforge version ranges are widened for the run (that
claim is exactly what is being checked). A version whose jar fails to load,
crashes or reports FAIL must not be claimed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

from panzer_mod import Mod, PanzerError
import panzer_versions

NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge"


def java_for(minecraft: str) -> int:
    return 25 if int(minecraft.split(".")[0]) >= 26 else 21


def plan(mod: Mod, releases: list[panzer_versions.Release], only: list[str] | None = None) -> list[dict]:
    """Per build version: the other Minecraft versions it claims, each with the NeoForge server to test on."""
    if not mod.config.get("compat"):
        print(f"{mod.name}: no [compat] table, nothing to check")
        return []
    newest = {r.minecraft: r.stable or r.latest for r in releases}
    builds = []
    for version in mod.stonecutter_versions:
        if only and version not in only:
            continue
        block = mod.config.get(version, {})
        own = block.get("minecraft_version", version)
        targets = []
        for mc in block.get("game_versions", [own]):
            if mc == own:
                continue
            if mc not in newest:
                raise PanzerError(f"{mod.name} {version}: claims {mc}, which has no NeoForge release")
            targets.append({"minecraft": mc, "neoforge": newest[mc], "java": java_for(mc)})
        if targets:
            builds.append({"version": version, "java": java_for(own), "targets": targets})
    return builds


def mod_jar(mod: Mod, version: str) -> Path:
    jar = mod.root / "build" / "libs" / mod.version / f"{mod.id}-{mod.version}+{version}.jar"
    if not jar.is_file():
        raise PanzerError(f"{jar} not built (./gradlew buildAndCollect -Pstonecutter.versions={version})")
    return jar


def dependency_jars(mod: Mod, version: str, minecraft: str, work: Path) -> list[Path]:
    """Required mods' jars: mavenLocal first (dependencies built from source), then their Maven repository."""
    jars = []
    for dep_id, dep in mod.depends_on.items():
        artifact = dep.get("artifact")
        if not artifact:
            continue
        # {mc} is the build version: the dependency jar built for the same Minecraft as the mod's.
        group, name = artifact.split(":")[0], artifact.split(":")[1].replace("{mc}", version)
        rel = Path(*group.split(".")) / name / dep["version"] / f"{name}-{dep['version']}.jar"
        local = Path.home() / ".m2" / "repository" / rel
        if local.is_file():
            jars.append(local)
            continue
        url = dep.get("maven", "").rstrip("/") + "/" + rel.as_posix()
        target = work / rel.name
        try:
            urllib.request.urlretrieve(url, target)
        except OSError as e:
            raise PanzerError(f"{dep_id}: not in mavenLocal and {url} failed: {e}") from e
        jars.append(target)
    return jars


_DEPENDENCY = re.compile(r"(\[\[dependencies\.[^\]]+\]\])(.*?)(?=\n\[|\Z)", re.S)


def widen_ranges(mods_toml: str) -> str:
    """Accept any Minecraft/NeoForge version: the claim under test."""
    def fix(m: re.Match) -> str:
        body = m.group(2)
        if re.search(r'modId\s*=\s*"(minecraft|neoforge)"', body):
            body = re.sub(r'versionRange\s*=\s*"[^"]*"', 'versionRange = "[0,)"', body)
        return m.group(1) + body
    return _DEPENDENCY.sub(fix, mods_toml)


def copy_widened(jar: Path, target: Path) -> None:
    with zipfile.ZipFile(jar) as src, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "META-INF/neoforge.mods.toml":
                data = widen_ranges(data.decode("utf-8")).encode("utf-8")
            out.writestr(info, data)


def java_home(java: int) -> str:
    home = os.environ.get(f"JAVA_HOME_{java}_X64") or (os.environ.get("JAVA_HOME") if java == 21 else None)
    if not home:
        raise PanzerError(f"no Java {java} (set JAVA_HOME_{java}_X64)")
    return home


def run(mod: Mod, version: str, target: dict, work: Path) -> tuple[bool, list[str]]:
    """Installs NeoForge `target` in `work`, runs the mod's check there; (passed, report lines)."""
    cfg = mod.config.get("compat") or {}
    mc, neoforge = target["minecraft"], target["neoforge"]
    server = work / f"server-{mc}"
    shutil.rmtree(server, ignore_errors=True)
    server.mkdir(parents=True)
    env = dict(os.environ, JAVA_HOME=java_home(target["java"]))
    env["PATH"] = f"{env['JAVA_HOME']}/bin{os.pathsep}{env['PATH']}"

    installer = work / f"neoforge-{neoforge}-installer.jar"
    if not installer.is_file():
        urllib.request.urlretrieve(f"{NEOFORGE_MAVEN}/{neoforge}/neoforge-{neoforge}-installer.jar", installer)
    out = subprocess.run(["java", "-jar", str(installer), "--installServer", str(server)],
                         cwd=server, env=env, capture_output=True, text=True)
    if out.returncode != 0:
        return False, [f"NeoForge {neoforge} installer failed:", *out.stdout.splitlines()[-20:], *out.stderr.splitlines()[-20:]]

    mods = server / "mods"
    mods.mkdir(exist_ok=True)
    for jar in [mod_jar(mod, version), *dependency_jars(mod, version, mc, work)]:
        copy_widened(jar, mods / jar.name)
    (server / "eula.txt").write_text("eula=true\n")
    (server / "server.properties").write_text("\n".join(cfg.get("server_properties", [])) + "\n")
    with open(server / "user_jvm_args.txt", "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(["-Xmx3G", *cfg.get("jvm_args", [])]) + "\n")

    timeout = 60 * int(cfg.get("timeout_minutes", 15))
    log = server / "compat-console.log"
    if not cfg.get("report"):
        return smoke(server, env, log, timeout)
    report = server / cfg["report"]
    with open(log, "w", encoding="utf-8") as console:
        try:
            subprocess.run(["bash", "run.sh", "nogui"], cwd=server, env=env, stdout=console,
                           stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
    if not report.is_file():
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
        return False, [f"no {cfg['report']}: the server did not finish the check", *tail]
    lines = report.read_text(encoding="utf-8").splitlines()
    return bool(lines) and lines[0].rstrip().endswith("PASS"), lines


def smoke(server: Path, env: dict, log: Path, timeout: int) -> tuple[bool, list[str]]:
    """Starts the server, stops it once it is up; passes if it got there and exited cleanly."""
    import threading
    proc = subprocess.Popen(["bash", "run.sh", "nogui"], cwd=server, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    started = threading.Event()
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    try:
        with open(log, "w", encoding="utf-8") as console:
            for line in proc.stdout:
                console.write(line)
                if not started.is_set() and "Done (" in line and "For help" in line:
                    started.set()
                    proc.stdin.write("stop\n")
                    proc.stdin.flush()
        code = proc.wait()
    finally:
        timer.cancel()
    text = log.read_text(encoding="utf-8", errors="replace")
    crashed = "---- Minecraft Crash Report ----" in text or "Exception in server tick loop" in text
    ok = started.is_set() and code == 0 and not crashed
    detail = [f"server {'started' if started.is_set() else 'never started'}, exit code {code}"
              + (", crash report" if crashed else "")]
    if not ok:
        detail += [l for l in text.splitlines() if "Exception" in l or "Error" in l][:15]
    return ok, detail


def run_all(mod: Mod, build: dict, work: Path) -> tuple[bool, list[str]]:
    ok, summary = True, []
    for target in build["targets"]:
        passed, lines = run(mod, build["version"], target, work)
        ok &= passed
        head = f"{mod.name} {build['version']} jar on Minecraft {target['minecraft']} (NeoForge {target['neoforge']}): " \
               + ("PASS" if passed else "FAIL")
        summary += [head, *(f"    {line}" for line in lines[:12])]
        print("\n".join(summary[-(1 + min(12, len(lines))):]), flush=True)
    return ok, summary


def plan_json(mod: Mod, only: list[str] | None = None) -> str:
    releases = panzer_versions.neoforge_releases(panzer_versions.fetch_neoforge_metadata())
    return json.dumps(plan(mod, releases, only))
