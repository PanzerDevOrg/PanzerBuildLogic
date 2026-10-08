#!/usr/bin/env python3
"""`panzer mspt`: how long does a real server tick take with many dropped items?
Starts a NeoForge dedicated server per configuration (no mods; the given mods
with Celeris's Java physics kernel; the same with its native kernel), spawns
--items item entities over forced-loaded flat ground and times the server with
vanilla's `tick sprint`, which runs ticks back to back and reports the average
milliseconds per tick.

    python3 tools/panzer_mspt.py --minecraft 1.21.1 --neoforge 21.1.248 --java 21 \\
        --mods celeris.jar velox.jar --items 5000 20000 --work build/mspt [--summary $GITHUB_STEP_SUMMARY]

For each item count, every configuration measures
    idle     the server with no items (its own baseline)
    falling  the first ticks after the items are thrown: falling, landing, sliding
    resting  long after, everything on the ground (vanilla still ticks each item)
The items are swords: they never stack, so every configuration keeps all of
them (merging would make the comparison depend on how fast piles collapse).
Results are printed as a table and written to <work>/mspt.json.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
from collections import Counter
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from panzer_mod import java_env, java_home  # noqa: E402
from panzer_join import Server, install_server, put_mods  # noqa: E402

SPRINT = re.compile(r"Sprint completed with ([\d.,]+) ticks per second, or ([\d.,]+) ms per tick")
GROUND = -60          # top of the default superflat ground (grass at y = -61)
AREA = 56             # items land in [-AREA, AREA) on x and z, inside the forced chunks
MARK = "panzer-mspt-mark"


def configurations(java: int, mods: list[str]) -> list[dict]:
    flags = ["--add-modules=jdk.incubator.vector"] + (["--enable-preview"] if java == 21 else [])
    return [
        {"name": "NeoForge, no mods", "mods": [], "jvm": []},
        {"name": "Velox, Java kernel", "mods": mods, "jvm": ["-Dceleris.physics.engine=java"]},
        {"name": "Velox, native kernel", "mods": mods, "jvm": flags},
    ]


def summon_lines(count: int, seed: int) -> list[str]:
    r = random.Random(seed)
    lines = []
    for _ in range(count):
        x, z = r.uniform(-AREA, AREA), r.uniform(-AREA, AREA)
        y = GROUND + r.uniform(4, 28)
        mx, my, mz = r.uniform(-0.4, 0.4), r.uniform(-0.2, 0.4), r.uniform(-0.4, 0.4)
        lines.append(f'summon minecraft:item {x:.3f} {y:.3f} {z:.3f} '
                     f'{{Item:{{id:"minecraft:diamond_sword",count:1}},Age:-32768,PickupDelay:32767,'
                     f'Motion:[{mx:.4f}d,{my:.4f}d,{mz:.4f}d]}}')
    return lines


class Console:
    """Commands into the server's stdin; waits for their output in its log."""

    def __init__(self, server: Server):
        self.server = server

    def send(self, *commands: str) -> None:
        for c in commands:
            self.server.proc.stdin.write(c + "\n")
        self.server.proc.stdin.flush()

    def wait_for(self, pattern: re.Pattern | str, start: int, timeout: float) -> re.Match | str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for line in self.server.lines[start:]:
                m = pattern.search(line) if isinstance(pattern, re.Pattern) else (line if pattern in line else None)
                if m:
                    return m
            if self.server.proc.poll() is not None:
                return None
            time.sleep(0.5)
        return None

    def sync(self, timeout: float = 600) -> bool:
        """Every command sent so far has run (the console runs them in order)."""
        start = len(self.server.lines)
        self.send(f"say {MARK}")
        return self.wait_for(MARK, start, timeout) is not None

    def sprint(self, ticks: int) -> float | None:
        start = len(self.server.lines)
        self.send(f"tick sprint {ticks}")
        m = self.wait_for(SPRINT, start, 60 * 30)
        return float(m.group(2).replace(",", ".")) if m else None


def server_pid() -> str | None:
    """The running server's JVM (run.sh starts it with @user_jvm_args.txt; one server at a time)."""
    out = subprocess.run(["pgrep", "-f", "user_jvm_args.txt"], capture_output=True, text=True).stdout.split()
    return out[-1] if out else None


def jcmd(java: int, pid: str, *command: str) -> None:
    subprocess.run([f"{java_home(java)}/bin/jcmd", pid, *command], capture_output=True, text=True, timeout=120)


FRAME = re.compile(r"^\s+(\S+)\(.*\)\s+line:")


def hot_methods(java: int, recording: Path, top: int = 25) -> list[tuple[str, float, float]]:
    """(method, % of samples on top of the stack, % anywhere in the stack) from a JFR file."""
    out = subprocess.run([f"{java_home(java)}/bin/jfr", "print", "--events", "jdk.ExecutionSample", "--stack-depth", "64",
                          str(recording)], capture_output=True, text=True).stdout
    self_count, total_count, samples = Counter(), Counter(), 0
    for event in out.split("jdk.ExecutionSample")[1:]:
        if "Server thread" not in event:
            continue
        frames = [m.group(1) for m in map(FRAME.match, event.splitlines()) if m]
        if not frames:
            continue
        samples += 1
        self_count[frames[0]] += 1
        for f in set(frames):
            total_count[f] += 1
    if not samples:
        return []
    return [(m, 100 * self_count[m] / samples, 100 * total_count[m] / samples)
            for m, _ in self_count.most_common(top)]


def measure(config: dict, args, work: Path, base: Path) -> dict:
    name = re.sub(r"[^\w.-]+", "-", config["name"])
    root = work / f"server-{name}"
    shutil.rmtree(root, ignore_errors=True)
    shutil.copytree(base, root, symlinks=True)
    put_mods(root / "mods", config["mods"])
    with open(root / "user_jvm_args.txt", "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(["-Xms4G", "-Xmx4G", *config["jvm"]]) + "\n")
    env = java_env(args.java)
    server = Server(root, env, ["bash", "run.sh", "nogui"])
    result = {"config": config["name"], "runs": []}
    if not server.start(60 * 10):
        server.stop()
        result["error"] = "server did not start: " + " | ".join(server.lines[-8:])
        return result
    console = Console(server)
    try:
        chunk = AREA // 16 + 1
        console.send("gamerule doDaylightCycle false", "gamerule doWeatherCycle false",
                     "gamerule doMobSpawning false", "gamerule randomTickSpeed 0",
                     f"forceload add {-chunk * 16} {-chunk * 16} {chunk * 16 - 1} {chunk * 16 - 1}")
        console.sync()
        console.sprint(200)  # JIT and chunk loading warm-up
        idle = console.sprint(args.idle_ticks)
        pid = server_pid() if args.jfr else None
        if pid:
            jcmd(args.java, pid, "JFR.start", "name=mspt", "settings=profile")
        for count in args.items:
            run = {"items": count, "idle_ms": idle}
            console.send("kill @e[type=minecraft:item]")
            console.send(*summon_lines(count, args.seed))
            console.sync()
            console.sprint(args.falling_ticks)  # warm-up: the first fall of this batch
            console.send("kill @e[type=minecraft:item]")
            console.send(*summon_lines(count, args.seed + 1))
            console.sync()
            run["falling_ms"] = console.sprint(args.falling_ticks)
            run["resting_ms"] = console.sprint(args.resting_ticks)
            result["runs"].append(run)
            print(f"  {config['name']}: {run}", flush=True)
        if pid:
            recording = root / "mspt.jfr"
            jcmd(args.java, pid, "JFR.stop", "name=mspt", f"filename={recording}")
            result["hot"] = hot_methods(args.java, recording)
        console.send("kill @e[type=minecraft:item]")
        engine = [line for line in server.lines if "Velox: items simulated" in line or "physics engine" in line.lower()]
        if engine:
            result["engine"] = engine[-1].split("]: ", 1)[-1]
    finally:
        server.stop()
    return result


def table(results: list[dict], header: str) -> list[str]:
    out = [header, "", "| Items | Configuration | Idle | Falling | Resting |", "|---|---|---|---|---|"]
    vanilla = {r["items"]: r for r in results[0].get("runs", [])} if results else {}
    for count in sorted({run["items"] for res in results for run in res.get("runs", [])}):
        for res in results:
            run = next((r for r in res.get("runs", []) if r["items"] == count), None)
            if run is None:
                continue
            cells = []
            for key in ("idle_ms", "falling_ms", "resting_ms"):
                v = run.get(key)
                base = vanilla.get(count, {}).get(key)
                text = "n/a" if v is None else f"{v:.2f} ms"
                if v and base and res is not results[0] and key != "idle_ms":
                    text += f" ({base / v:.1f}× faster)"
                cells.append(text)
            out.append(f"| {count:,} | {res['config']} | " + " | ".join(cells) + " |")
    for res in results:
        if res.get("hot"):
            out += ["", f"<details><summary>{res['config']}: where the server thread spends its time (JFR)</summary>", "",
                    "| Method | Self | Total |", "|---|---|---|"]
            out += [f"| `{m}` | {s:.1f}% | {t:.1f}% |" for m, s, t in res["hot"]]
            out += ["", "</details>"]
    for res in results:
        if res.get("error"):
            out.append(f"\n**{res['config']}**: {res['error']}")
        if res.get("engine"):
            out.append(f"\n{res['config']}: `{res['engine']}`")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--minecraft", required=True)
    p.add_argument("--neoforge", required=True)
    p.add_argument("--java", type=int, required=True)
    p.add_argument("--mods", nargs="+", required=True, help="jars for the modded configurations (Celeris, Velox)")
    p.add_argument("--items", nargs="+", type=int, default=[5000, 20000])
    p.add_argument("--idle-ticks", type=int, default=400)
    p.add_argument("--falling-ticks", type=int, default=80)
    p.add_argument("--resting-ticks", type=int, default=1200)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--jfr", action="store_true", help="profile the server thread with Java Flight Recorder during the runs")
    p.add_argument("--work", required=True, type=Path)
    p.add_argument("--summary", type=Path)
    args = p.parse_args(argv)
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    env = java_env(args.java)
    base = work / "base"
    if not (base / "run.sh").is_file():
        install_server(base, args.neoforge, env, work)
        jvm = base / "user_jvm_args.txt"
        jvm.write_text("\n".join(l for l in jvm.read_text().splitlines() if not l.startswith("-Xmx")) + "\n")
    results = []
    for config in configurations(args.java, args.mods):
        print(f"{config['name']}", flush=True)
        results.append(measure(config, args, work, base))
    (work / "mspt.json").write_text(json.dumps({"minecraft": args.minecraft, "neoforge": args.neoforge,
                                                 "java": args.java, "results": results}, indent=1))
    lines = table(results, f"## Server tick time: Minecraft {args.minecraft}, NeoForge {args.neoforge}, Java {args.java}")
    print("\n".join(lines))
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 0 if all(not r.get("error") and r["runs"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
