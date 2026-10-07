#!/usr/bin/env python3
"""`panzer join`: can a player get into the server? Each scenario starts a NeoForge
dedicated server with some jars in its mods/, then a game client (a throwaway
ModDevGradle run, under Xvfb when there is no display) with other jars, which
joins it through Quick Play. The server log decides: the player joined, or the
connection was refused (and why).

    python3 tools/panzer_join.py --minecraft 1.21.1 --neoforge 21.1.248 --java 21 \\
        --scenarios scenarios.json --work build/join [--summary $GITHUB_STEP_SUMMARY]

scenarios.json is a list of
    {"name": "...", "server": ["a.jar", ...], "client": ["b.jar", ...], "expect": "join" | "refused",
     "client_loader": "neoforge" | "vanilla"}

A "vanilla" client is plain Minecraft, no NeoForge (ModDevGradle's NeoForm-only
mode, --neoform <version>); it takes no mods. "server_loader": "vanilla" is
Mojang's own server jar for --minecraft, also without mods. "server_loader" /
"client_loader": "fabric" run Fabric Loader (--fabric-loader) with Fabric API
(--fabric-api) added to the scenario's jars: the server through Fabric's server
launcher, the client as a Fabric Loom run. "server_log": strings the server log
must contain (e.g. a mod's startup line), or the scenario fails; "client_log"
the same for the client.

A scenario passes when the outcome is the expected one; "refused" scenarios are
controls that show the check can fail (e.g. a mod with a required network
channel on one side only).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from panzer_compat import BUILD_LOGIC, NEOFORGE_MAVEN, java_home, moddev_version  # noqa: E402

PORT = 25565
JOINED = re.compile(r"joined the game|logged in with entity id")
REFUSED = re.compile(r"lost connection|Disconnecting|disconnect|negotiation|incompatible", re.I)

_SETTINGS = """pluginManagement {
    repositories {
        gradlePluginPortal()
        maven("https://maven.neoforged.net/releases/")
    }
}
rootProject.name = "join-client"
"""

_BUILD = """plugins { id("net.neoforged.moddev") version "%(moddev)s" }
java { toolchain { languageVersion = JavaLanguageVersion.of(%(java)d) } }
neoForge {
    %(loader)s
    runs {
        create("join") {
            client()
            gameDirectory = file("run")
            jvmArguments.add("-Xmx2G")
            programArguments.addAll("--quickPlayMultiplayer", "localhost:%(port)d")
        }
    }
}
"""


class Server:
    """A dedicated server in `root` (NeoForge's run.sh, or Mojang's server.jar), its console mirrored to a log and scanned."""

    def __init__(self, root: Path, env: dict, command: list[str]):
        self.root, self.env, self.command = root, env, command
        self.lines: list[str] = []
        self.done = threading.Event()
        self.proc: subprocess.Popen | None = None

    def start(self, timeout: int) -> bool:
        self.proc = subprocess.Popen(self.command, cwd=self.root, env=self.env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=self._read, daemon=True).start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.done.wait(2):
                return True
            if self.proc.poll() is not None:
                return False  # it stopped before it was up (e.g. a mod refused to load)
        return False

    def _read(self) -> None:
        with open(self.root / "join-console.log", "w", encoding="utf-8") as log:
            for line in self.proc.stdout:
                log.write(line)
                log.flush()
                self.lines.append(line.rstrip())
                if "Done (" in line and "For help" in line:
                    self.done.set()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write("stop\n")
                self.proc.stdin.flush()
                self.proc.wait(120)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()


def install_server(root: Path, neoforge: str, env: dict, work: Path) -> None:
    installer = work / f"neoforge-{neoforge}-installer.jar"
    if not installer.is_file():
        urllib.request.urlretrieve(f"{NEOFORGE_MAVEN}/{neoforge}/neoforge-{neoforge}-installer.jar", installer)
    root.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(["java", "-jar", str(installer), "--installServer", str(root)],
                         cwd=root, env=env, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError("NeoForge installer failed:\n" + out.stdout[-2000:] + out.stderr[-2000:])
    write_server_files(root)
    with open(root / "user_jvm_args.txt", "a", encoding="utf-8") as f:
        f.write("\n-Xmx2G\n")


VERSION_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


def install_vanilla_server(root: Path, minecraft: str, work: Path) -> None:
    """Mojang's server jar for `minecraft`, from the launcher's version manifest."""
    jar = work / f"minecraft-server-{minecraft}.jar"
    if not jar.is_file():
        with urllib.request.urlopen(VERSION_MANIFEST) as r:
            versions = json.load(r)["versions"]
        entry = next((v for v in versions if v["id"] == minecraft), None)
        if entry is None:
            raise RuntimeError(f"Minecraft {minecraft} is not in Mojang's version manifest")
        with urllib.request.urlopen(entry["url"]) as r:
            server = json.load(r)["downloads"]["server"]["url"]
        urllib.request.urlretrieve(server, jar)
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(jar, root / "server.jar")
    write_server_files(root)


FABRIC_META = "https://meta.fabricmc.net/v2/versions"
FABRIC_MAVEN = "https://maven.fabricmc.net"


def fabric_api_jar(version: str, work: Path) -> str:
    jar = work / f"fabric-api-{version}.jar"
    if not jar.is_file():
        urllib.request.urlretrieve(f"{FABRIC_MAVEN}/net/fabricmc/fabric-api/fabric-api/{version}/fabric-api-{version}.jar", jar)
    return str(jar)


def install_fabric_server(root: Path, minecraft: str, loader: str, work: Path) -> None:
    """Fabric's server launcher for `minecraft` + `loader`: on first start it downloads
    Mojang's server jar and the loader's libraries, then runs the game."""
    jar = work / f"fabric-server-{minecraft}-{loader}.jar"
    if not jar.is_file():
        with urllib.request.urlopen(f"{FABRIC_META}/installer") as r:
            installer = next(i["version"] for i in json.load(r) if i.get("stable"))
        urllib.request.urlretrieve(f"{FABRIC_META}/loader/{minecraft}/{loader}/{installer}/server/jar", jar)
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(jar, root / "fabric-server-launch.jar")
    write_server_files(root)


def write_server_files(root: Path) -> None:
    (root / "eula.txt").write_text("eula=true\n")
    (root / "server.properties").write_text("\n".join([
        "online-mode=false", "enforce-secure-profile=false", f"server-port={PORT}",
        "level-type=minecraft\\:flat", "spawn-npcs=false", "spawn-animals=false", "spawn-monsters=false",
        "generate-structures=false", "view-distance=4", "simulation-distance=4",
    ]) + "\n")


def client_project(root: Path, loader: str, java: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "gradlew").is_file():
        shutil.copy2(BUILD_LOGIC / "gradlew", root / "gradlew")
        shutil.copytree(BUILD_LOGIC / "gradle", root / "gradle")
    (root / "settings.gradle.kts").write_text(_SETTINGS)
    (root / "build.gradle.kts").write_text(_BUILD % {"moddev": moddev_version(), "java": java,
                                                     "loader": loader, "port": PORT})
    game = root / "run"
    (game / "config").mkdir(parents=True, exist_ok=True)
    # Same as panzer compat's client runs: FML's early window is flaky under Xvfb.
    (game / "config" / "fml.toml").write_text("earlyWindowControl = false\n")
    (game / "options.txt").write_text("onboardAccessibility:false\nnarrator:0\npauseOnLostFocus:false\n")


_FABRIC_SETTINGS = """pluginManagement {
    repositories {
        gradlePluginPortal()
        maven("https://maven.fabricmc.net/")
    }
}
rootProject.name = "join-client-fabric"
"""

_FABRIC_BUILD = """plugins { id("%(plugin)s") version "%(loom)s" }
java { toolchain { languageVersion = JavaLanguageVersion.of(%(java)d) } }
dependencies {
    minecraft("com.mojang:minecraft:%(minecraft)s")
    %(mappings)s
    %(conf)s("net.fabricmc:fabric-loader:%(loader)s")
}
loom {
    runs {
        named("client") {
            runDir("run")
            vmArg("-Xmx2G")
            programArgs("--quickPlayMultiplayer", "localhost:%(port)d")
        }
    }
}
"""


def loom_version() -> str:
    for line in (BUILD_LOGIC / "common.stonecutter.properties.toml").read_text().splitlines():
        if line.strip().startswith("loom"):
            return line.split("=", 1)[1].strip().strip('"')
    raise RuntimeError("[plugins] loom is missing in common.stonecutter.properties.toml")


def fabric_client_project(root: Path, minecraft: str, loader: str, java: int) -> None:
    """A Loom project whose client run joins localhost; mods go in run/mods (Fabric
    Loader remaps release jars to the development names at launch)."""
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "gradlew").is_file():
        shutil.copy2(BUILD_LOGIC / "gradlew", root / "gradlew")
        shutil.copytree(BUILD_LOGIC / "gradle", root / "gradle")
    unobfuscated = int(minecraft.split(".")[0]) >= 26
    (root / "settings.gradle.kts").write_text(_FABRIC_SETTINGS)
    (root / "build.gradle.kts").write_text(_FABRIC_BUILD % {
        "plugin": "net.fabricmc.fabric-loom" if unobfuscated else "net.fabricmc.fabric-loom-remap",
        "loom": loom_version(), "java": java, "minecraft": minecraft, "loader": loader, "port": PORT,
        "mappings": "" if unobfuscated else "mappings(loom.officialMojangMappings())",
        "conf": "implementation" if unobfuscated else "modImplementation"})
    game = root / "run"
    game.mkdir(parents=True, exist_ok=True)
    (game / "options.txt").write_text("onboardAccessibility:false\nnarrator:0\npauseOnLostFocus:false\n")


def put_mods(mods: Path, jars: list[str]) -> None:
    shutil.rmtree(mods, ignore_errors=True)
    mods.mkdir(parents=True)
    for jar in jars:
        shutil.copy2(jar, mods / Path(jar).name)


def start_client(root: Path, log: Path, fabric: bool = False) -> subprocess.Popen:
    # Fabric Loom 1.18 itself needs Gradle on Java 25.
    env = dict(os.environ, JAVA_HOME=java_home(25 if fabric else 21))
    env["PATH"] = f"{env['JAVA_HOME']}/bin{os.pathsep}{env['PATH']}"
    env.setdefault("SDL_VIDEO_FORCE_EGL", "1")
    jdks = ",".join(k for k in ("JAVA_HOME_21_X64", "JAVA_HOME_25_X64") if k in os.environ)
    command = ["bash", "gradlew", "runClient" if fabric else "runJoin", "--no-daemon", "--console=plain", "--no-watch-fs"]
    if jdks:
        command.append(f"-Porg.gradle.java.installations.fromEnv={jdks}")
    if not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        command = ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24", *command]
    return subprocess.Popen(command, cwd=root, env=env, stdout=open(log, "w", encoding="utf-8"),
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)


CLIENT_FATAL = ("Failed to start FML", "---- Minecraft Crash Report ----", "ModLoadingException",
                "Incompatible mods found", "Mod resolution failed")


def client_failed(log: Path) -> bool:
    """The game never got as far as joining: mod loading failed or it crashed."""
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(marker in text for marker in CLIENT_FATAL)


def kill_tree(proc: subprocess.Popen) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(20)
            return
        except subprocess.TimeoutExpired:
            continue


def run_scenario(s: dict, args, work: Path) -> tuple[bool, list[str]]:
    name = re.sub(r"[^\w.-]+", "-", s["name"])
    server_env = dict(os.environ, JAVA_HOME=java_home(args.java))
    server_env["PATH"] = f"{server_env['JAVA_HOME']}/bin{os.pathsep}{server_env['PATH']}"
    server_root = work / f"server-{name}"
    shutil.rmtree(server_root, ignore_errors=True)
    server_loader = s.get("server_loader", "neoforge")
    vanilla_server = server_loader == "vanilla"
    if (server_loader == "fabric" or s.get("client_loader") == "fabric") and not (args.fabric_loader and args.fabric_api):
        return False, ["Fabric scenarios need --fabric-loader and --fabric-api"]
    if vanilla_server:
        if s.get("server"):
            return False, ["a vanilla server takes no mods"]
        install_vanilla_server(server_root, args.minecraft, work)
        command = ["java", "-Xmx2G", "-jar", "server.jar", "nogui"]
    elif server_loader == "fabric":
        install_fabric_server(server_root, args.minecraft, args.fabric_loader, work)
        put_mods(server_root / "mods", [*s.get("server", []), fabric_api_jar(args.fabric_api, work)])
        command = ["java", "-Xmx2G", "-jar", "fabric-server-launch.jar", "nogui"]
    else:
        install_server(server_root, args.neoforge, server_env, work)
        put_mods(server_root / "mods", s.get("server", []))
        command = ["bash", "run.sh", "nogui"]
    client_loader = s.get("client_loader", "neoforge")
    vanilla = client_loader == "vanilla"
    fabric_client = client_loader == "fabric"
    if vanilla and not args.neoform:
        return False, ["a vanilla client needs --neoform"]
    if fabric_client:
        client_root = work / "client-fabric"
        fabric_client_project(client_root, args.minecraft, args.fabric_loader, args.java)
        put_mods(client_root / "run" / "mods", [*s.get("client", []), fabric_api_jar(args.fabric_api, work)])
    else:
        client_root = work / ("client-vanilla" if vanilla else "client")
        client_project(client_root, f'neoFormVersion = "{args.neoform}"' if vanilla else f'version = "{args.neoforge}"',
                       args.java)
        put_mods(client_root / "run" / "mods", s.get("client", []))
    shutil.rmtree(client_root / "run" / "logs", ignore_errors=True)

    server = Server(server_root, server_env, command)
    detail = [("vanilla server;" if vanilla_server else
               f"{server_loader} server mods: {', '.join(Path(j).name for j in s.get('server', [])) or 'none'};")
              + (f" vanilla client (NeoForm {args.neoform})" if vanilla else
                 f" {client_loader} client mods: {', '.join(Path(j).name for j in s.get('client', [])) or 'none'}")]
    if not server.start(60 * 10):
        server.stop()
        return False, detail + ["the server never finished starting", *server.lines[-15:]]
    client_log = work / f"client-{name}.log"
    seen = len(server.lines)  # only what happens once the client is on its way
    client = start_client(client_root, client_log, fabric_client)
    outcome, deadline = None, time.monotonic() + 60 * args.timeout_minutes
    try:
        while outcome is None and time.monotonic() < deadline:
            time.sleep(2)
            for line in server.lines[seen:]:
                if JOINED.search(line):
                    outcome = "join"
                elif "/INFO]" in line or "/WARN]" in line or "/ERROR]" in line:
                    if REFUSED.search(line) and outcome is None:
                        detail.append("server: " + line.split("]: ", 1)[-1])
                        outcome = "refused"
            seen = len(server.lines)
            if outcome is None and client_failed(client_log):
                outcome = "client failed to start"
            if client.poll() is not None and outcome is None:
                outcome = "client exited"
    finally:
        kill_tree(client)
        server.stop()
    if outcome is None:
        outcome = "timeout"
    if outcome in ("client exited", "client failed to start", "timeout"):
        tail = client_log.read_text(encoding="utf-8", errors="replace").splitlines()[-25:] if client_log.is_file() else []
        detail += [f"client: {outcome}", *tail]
    ok = outcome == s["expect"]
    server_text = "\n".join(server.lines)
    for wanted in s.get("server_log", []):
        if wanted not in server_text:
            ok = False
            detail.append(f"server log lacks: {wanted}")
    client_text = client_log.read_text(encoding="utf-8", errors="replace") if client_log.is_file() else ""
    for wanted in s.get("client_log", []):
        if wanted not in client_text:
            ok = False
            detail.append(f"client log lacks: {wanted}")
    detail.insert(0, f"outcome: {outcome} (expected {s['expect']})")
    return ok, detail


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--minecraft", required=True)
    p.add_argument("--neoforge", required=True)
    p.add_argument("--java", type=int, required=True)
    p.add_argument("--neoform", help="NeoForm version for vanilla clients")
    p.add_argument("--fabric-loader", help="Fabric Loader version for Fabric scenarios")
    p.add_argument("--fabric-api", help="Fabric API version for Fabric scenarios")
    p.add_argument("--scenarios", required=True, type=Path)
    p.add_argument("--work", required=True, type=Path)
    p.add_argument("--timeout-minutes", type=int, default=12, help="per client, from its launch")
    p.add_argument("--summary", type=Path)
    args = p.parse_args(argv)
    args.work.mkdir(parents=True, exist_ok=True)
    scenarios = json.loads(args.scenarios.read_text())
    ok, summary = True, [f"## Join test: Minecraft {args.minecraft} (NeoForge {args.neoforge})", ""]
    for s in scenarios:
        try:
            passed, lines = run_scenario(s, args, args.work.resolve())
        except Exception as e:  # noqa: BLE001 - reported as the scenario's failure
            passed, lines = False, [f"error: {e}"]
        ok &= passed
        block = [f"{'PASS' if passed else 'FAIL'} {s['name']}", *(f"    {line}" for line in lines)]
        print("\n".join(block), flush=True)
        summary += ["```", *block, "```"]
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write("\n".join(summary) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
