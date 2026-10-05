#!/usr/bin/env python3
"""`panzer join`: can a player get into the server? Each scenario starts a NeoForge
dedicated server with some jars in its mods/, then a game client (a throwaway
ModDevGradle run, under Xvfb when there is no display) with other jars, which
joins it through Quick Play. The server log decides: the player joined, or the
connection was refused (and why).

    python3 tools/panzer_join.py --minecraft 1.21.1 --neoforge 21.1.248 --java 21 \\
        --scenarios scenarios.json --work build/join [--summary $GITHUB_STEP_SUMMARY]

scenarios.json is a list of
    {"name": "...", "server": ["a.jar", ...], "client": ["b.jar", ...], "expect": "join" | "refused"}

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
    version = "%(neoforge)s"
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
    """A NeoForge dedicated server in `root`, its console mirrored to a log and scanned."""

    def __init__(self, root: Path, env: dict):
        self.root, self.env = root, env
        self.lines: list[str] = []
        self.done = threading.Event()
        self.proc: subprocess.Popen | None = None

    def start(self, timeout: int) -> bool:
        self.proc = subprocess.Popen(["bash", "run.sh", "nogui"], cwd=self.root, env=self.env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=self._read, daemon=True).start()
        return self.done.wait(timeout)

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
    (root / "eula.txt").write_text("eula=true\n")
    (root / "server.properties").write_text("\n".join([
        "online-mode=false", "enforce-secure-profile=false", f"server-port={PORT}",
        "level-type=minecraft\\:flat", "spawn-npcs=false", "spawn-animals=false", "spawn-monsters=false",
        "generate-structures=false", "view-distance=4", "simulation-distance=4",
    ]) + "\n")
    with open(root / "user_jvm_args.txt", "a", encoding="utf-8") as f:
        f.write("\n-Xmx2G\n")


def client_project(root: Path, neoforge: str, java: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "gradlew").is_file():
        shutil.copy2(BUILD_LOGIC / "gradlew", root / "gradlew")
        shutil.copytree(BUILD_LOGIC / "gradle", root / "gradle")
    (root / "settings.gradle.kts").write_text(_SETTINGS)
    (root / "build.gradle.kts").write_text(_BUILD % {"moddev": moddev_version(), "java": java,
                                                     "neoforge": neoforge, "port": PORT})
    game = root / "run"
    (game / "config").mkdir(parents=True, exist_ok=True)
    # Same as panzer compat's client runs: FML's early window is flaky under Xvfb.
    (game / "config" / "fml.toml").write_text("earlyWindowControl = false\n")
    (game / "options.txt").write_text("onboardAccessibility:false\nnarrator:0\npauseOnLostFocus:false\n")


def put_mods(mods: Path, jars: list[str]) -> None:
    shutil.rmtree(mods, ignore_errors=True)
    mods.mkdir(parents=True)
    for jar in jars:
        shutil.copy2(jar, mods / Path(jar).name)


def start_client(root: Path, log: Path) -> subprocess.Popen:
    env = dict(os.environ, JAVA_HOME=java_home(21))
    env["PATH"] = f"{env['JAVA_HOME']}/bin{os.pathsep}{env['PATH']}"
    env.setdefault("SDL_VIDEO_FORCE_EGL", "1")
    jdks = ",".join(k for k in ("JAVA_HOME_21_X64", "JAVA_HOME_25_X64") if k in os.environ)
    command = ["bash", "gradlew", "runJoin", "--no-daemon", "--console=plain", "--no-watch-fs"]
    if jdks:
        command.append(f"-Porg.gradle.java.installations.fromEnv={jdks}")
    if not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        command = ["xvfb-run", "-a", "-s", "-screen 0 1280x720x24", *command]
    return subprocess.Popen(command, cwd=root, env=env, stdout=open(log, "w", encoding="utf-8"),
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)


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
    install_server(server_root, args.neoforge, server_env, work)
    put_mods(server_root / "mods", s.get("server", []))
    client_root = work / "client"
    client_project(client_root, args.neoforge, args.java)
    put_mods(client_root / "run" / "mods", s.get("client", []))
    shutil.rmtree(client_root / "run" / "logs", ignore_errors=True)

    server = Server(server_root, server_env)
    detail = [f"server mods: {', '.join(Path(j).name for j in s.get('server', [])) or 'none'};"
              f" client mods: {', '.join(Path(j).name for j in s.get('client', [])) or 'none'}"]
    if not server.start(60 * 10):
        server.stop()
        return False, detail + ["the server never finished starting", *server.lines[-15:]]
    client_log = work / f"client-{name}.log"
    seen = len(server.lines)  # only what happens once the client is on its way
    client = start_client(client_root, client_log)
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
            if client.poll() is not None and outcome is None:
                outcome = "client exited"
    finally:
        kill_tree(client)
        server.stop()
    if outcome is None:
        outcome = "timeout"
    if outcome in ("client exited", "timeout"):
        tail = client_log.read_text(encoding="utf-8", errors="replace").splitlines()[-25:] if client_log.is_file() else []
        detail += [f"client: {outcome}", *tail]
    ok = outcome == s["expect"]
    detail.insert(0, f"outcome: {outcome} (expected {s['expect']})")
    return ok, detail


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--minecraft", required=True)
    p.add_argument("--neoforge", required=True)
    p.add_argument("--java", type=int, required=True)
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
