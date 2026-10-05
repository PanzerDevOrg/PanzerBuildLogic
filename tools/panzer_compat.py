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

A client-only mod (`side = "client"`) is run in a real game client instead: a
throwaway ModDevGradle project per version (`runCompat`, NeoForge `target`),
the jars in its `run/mods`, under Xvfb when there is no display. Its check
must stop the client and write `report` in the game directory:

    [compat]
    side = "client"
    jvm_args = ["-Dtessera.selftest=true", "-Xmx2G"]
    report = "tessera-selftest.txt"
    client_options = ["onboardAccessibility:false", "narrator:0"]   # options.txt

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


def dependency_build(dep: dict, version: str, minecraft: str) -> str:
    """The dependency's build that claims `minecraft` (its TOML on GitHub), else the mod's build version.

    A player on 1.21.6 installs the dependency file made for 1.21.6, which need not
    be the one built alongside this jar (Celeris's 1.21.1 build, not its 1.21.10 one).
    """
    repo = dep.get("repo")
    if not repo:
        return version
    url = f"https://api.github.com/repos/{repo}/contents/mod.stonecutter.properties.toml"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github.raw"})
    token = os.environ.get("GH_TOKEN", "").strip()
    if token:
        request.add_header("Authorization", f"token {token}")
    try:
        import tomllib
        with urllib.request.urlopen(request, timeout=30) as response:
            config = tomllib.loads(response.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - any failure: the build version, as before
        print(f"{repo}: could not read its TOML ({e}); using its {version} build")
        return version
    for build, block in config.items():
        if isinstance(block, dict) and minecraft in block.get("game_versions", []):
            return build
    return version


def dependency_jars(mod: Mod, version: str, minecraft: str, work: Path) -> list[Path]:
    """Required mods' jars for `minecraft`: mavenLocal first (built from source), then their Maven repository."""
    jars = []
    for dep_id, dep in mod.depends_on.items():
        artifact = dep.get("artifact")
        if not artifact:
            continue
        # {mc} is the dependency's build that claims this Minecraft version.
        build = dependency_build(dep, version, minecraft)
        group, name = artifact.split(":")[0], artifact.split(":")[1].replace("{mc}", build)
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
    if cfg.get("side") == "client":
        return run_client(mod, version, target, work)
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
        return False, [f"no {cfg['report']}: the server did not finish the check", *failure_lines(server, log)]
    lines = report.read_text(encoding="utf-8").splitlines()
    return bool(lines) and lines[0].rstrip().endswith("PASS"), lines


BUILD_LOGIC = Path(__file__).resolve().parents[1]

_CLIENT_SETTINGS = """pluginManagement {
    repositories {
        gradlePluginPortal()
        maven("https://maven.neoforged.net/releases/")
    }
}
rootProject.name = "compat-client"
"""

_CLIENT_BUILD = """plugins { id("net.neoforged.moddev") version "%(moddev)s" }
java { toolchain { languageVersion = JavaLanguageVersion.of(%(java)d) } }
neoForge {
    version = "%(neoforge)s"
    runs {
        create("compat") {
            client()
            gameDirectory = file("run")
            jvmArguments.addAll(%(jvm_args)s)
        }
    }
}
"""


def moddev_version() -> str:
    m = re.search(r'^moddev\s*=\s*"([^"]+)"', (BUILD_LOGIC / "common.stonecutter.properties.toml").read_text(), re.M)
    if not m:
        raise PanzerError("no [plugins] moddev in the common TOML")
    return m.group(1)


def run_client(mod: Mod, version: str, target: dict, work: Path) -> tuple[bool, list[str]]:
    """A game client of `target` (ModDevGradle run) with the jars in its mods/; the mod's check reports."""
    cfg = mod.config.get("compat") or {}
    if not cfg.get("report"):
        raise PanzerError(f"{mod.name}: a client compat run needs [compat] report")
    mc, neoforge = target["minecraft"], target["neoforge"]
    project = work / f"client-{mc}"
    shutil.rmtree(project, ignore_errors=True)
    game = project / "run"
    (game / "mods").mkdir(parents=True)
    shutil.copy2(BUILD_LOGIC / "gradlew", project / "gradlew")
    shutil.copytree(BUILD_LOGIC / "gradle", project / "gradle")
    (project / "settings.gradle.kts").write_text(_CLIENT_SETTINGS)
    (project / "build.gradle.kts").write_text(_CLIENT_BUILD % {
        "moddev": moddev_version(), "java": target["java"], "neoforge": neoforge,
        "jvm_args": "listOf(" + ", ".join(json.dumps(a) for a in cfg.get("jvm_args", [])) + ")",
    })
    for jar in [mod_jar(mod, version), *dependency_jars(mod, version, mc, work)]:
        copy_widened(jar, game / "mods" / jar.name)
    if cfg.get("client_options"):
        (game / "options.txt").write_text("\n".join(cfg["client_options"]) + "\n")
    # FML's early loading window is flaky under Xvfb (an NPE reading its own config in
    # GlDebug on 21.7/21.8 runs); it is not what is being checked.
    (game / "config").mkdir(exist_ok=True)
    (game / "config" / "fml.toml").write_text("earlyWindowControl = false\n")

    env = dict(os.environ, JAVA_HOME=java_home(21))
    env["PATH"] = f"{env['JAVA_HOME']}/bin{os.pathsep}{env['PATH']}"
    # 26.3+ opens its window through SDL3, whose GLX visual matching finds nothing on
    # Xvfb + Mesa; EGL (what Mesa offers there anyway) works. GLFW-era versions ignore it.
    env.setdefault("SDL_VIDEO_FORCE_EGL", "1")
    jdks = ",".join(k for k in ("JAVA_HOME_21_X64", "JAVA_HOME_25_X64") if k in os.environ)
    command = ["bash", "gradlew", "runCompat", "--no-daemon", "--console=plain", "--no-watch-fs"]
    if jdks:
        command.append(f"-Porg.gradle.java.installations.fromEnv={jdks}")
    if not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        command = ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24", *command]

    log = game / "compat-console.log"
    with open(log, "w", encoding="utf-8") as console:
        try:
            subprocess.run(command, cwd=project, env=env, stdout=console, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=60 * int(cfg.get("timeout_minutes", 20)))
        except subprocess.TimeoutExpired:
            pass
    report = game / cfg["report"]
    if not report.is_file():
        return False, [f"no {cfg['report']}: the client did not finish the check", *failure_lines(game, log),
                       *mod_log_lines(mod, log)]
    lines = report.read_text(encoding="utf-8").splitlines()
    passed = bool(lines) and lines[0].rstrip().endswith("PASS")
    return passed, lines if passed else [*lines, *mod_log_lines(mod, log)]


def mod_log_lines(mod: Mod, log: Path, limit: int = 40) -> list[str]:
    """The mod's own WARN/ERROR lines in a console log, each with the exception lines that follow it."""
    if not log.is_file():
        return []
    out, follow = [], 0
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if mod.name in line and re.search(r"/(WARN|ERROR)\]", line):
            out.append(line)
            follow = 8
        elif follow and (line.startswith(("\t", " ")) or re.match(r"[\w.$]+(Exception|Error)", line)):
            out.append(line)
            follow -= 1
        else:
            follow = 0
        if len(out) >= limit:
            break
    return out


def failure_lines(server: Path, log: Path) -> list[str]:
    """Why a game stopped: each exception of its crash report (or console) with its first frames, then the console's end."""
    reports = sorted((server / "crash-reports").glob("*.txt")) if (server / "crash-reports").is_dir() else []
    source = reports[-1] if reports else log
    text = source.read_text(encoding="utf-8", errors="replace").splitlines() if source.is_file() else []
    causes, frames = [], 0
    for line in text:
        if re.search(r"Exception|Error:|Caused by|Description:|FAILED during", line):
            causes.append(line)
            frames = 6
        elif frames and line.lstrip().startswith("at "):
            causes.append(line)
            frames -= 1
        else:
            frames = 0
        if len(causes) >= 45:
            break
    tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-10:] if log.is_file() else []
    return [f"({source.name})", *causes, "...", *tail]


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
        detail += failure_lines(server, log)
    return ok, detail


def run_all(mod: Mod, build: dict, work: Path) -> tuple[bool, list[str]]:
    ok, summary = True, []
    for target in build["targets"]:
        passed, lines = run(mod, build["version"], target, work)
        ok &= passed
        head = f"{mod.name} {build['version']} jar on Minecraft {target['minecraft']} (NeoForge {target['neoforge']}): " \
               + ("PASS" if passed else "FAIL")
        shown = lines[:70]
        summary += [head, *(f"    {line}" for line in shown)]
        print("\n".join(summary[-(1 + len(shown)):]), flush=True)
    return ok, summary


def plan_json(mod: Mod, only: list[str] | None = None) -> str:
    releases = panzer_versions.neoforge_releases(panzer_versions.fetch_neoforge_metadata())
    return json.dumps(plan(mod, releases, only))
