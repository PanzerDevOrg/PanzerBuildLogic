#!/usr/bin/env python3
"""panzer: one tool for every mod built on panzer-build-logic.

  panzer mods                      list the mods in mods.toml, their checkout and sync state
  panzer sync [MOD...|--all]       write the shared files (wrapper, settings, CI callers,
                                   licenses, NOTICE, README blocks, .gitignore block)
  panzer check [MOD...|--all]      same, but only report what differs (exit 1 if anything)
  panzer new DIR --id ID           scaffold a new mod next to panzer-build-logic, then sync it
  panzer release MOD [--push]      tag v<version> (changelog required); CI publishes the tag
  panzer secrets [--org ORG]       copy tokens from .env into GitHub Actions secrets (gh CLI);
                                   PANZER_SYNC_TOKEN only goes to panzer-build-logic
  panzer publish ...               the release publisher (publishing/panzer_publish.py)
  panzer doctor                    check tools, tokens and checkouts
  panzer token-check               can PANZER_SYNC_TOKEN read, push and tag every mod?
  panzer versions [list]           Minecraft versions, what each mod builds, what is downloaded
  panzer versions use 1.21.10      work on some versions only (the rest is never configured or downloaded)
  panzer versions all              back to every version (and the committed active version)
  panzer versions prefetch         download and decompile every version once, for all mods
  panzer versions status|clean     local cache size; remove NeoForge versions no mod uses
  panzer versions neoforge [1.21]  NeoForge builds published per Minecraft version (to bump the matrix)
  panzer versions fabric [1.21]    Fabric API per Minecraft version, newest Fabric Loader and Loom
  panzer compat plan|run           check a built jar on the other Minecraft versions it claims
  panzer ci plan|verify-jars ...   used by .github/workflows/mod-ci.yml

MOD is a path to a mod checkout or a key from mods.toml (celeris, tessera, ...).
Settings and tokens come from the environment or from .env (in the current
directory, the mod, or panzer-build-logic); see .env.example.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "publishing"))

import panzer_ci  # noqa: E402
import panzer_sync  # noqa: E402
from panzer_mod import BUILD_LOGIC, MOD_TOML, PanzerError, load_dotenv, load_mod, registry  # noqa: E402

SECRETS = ("MODRINTH_TOKEN", "CURSEFORGE_TOKEN", "PANZER_SYNC_TOKEN")


def resolve_mods(names: list[str], all_mods: bool) -> list[Path]:
    known = {m.key: m for m in registry()}
    if all_mods:
        missing = [m.key for m in known.values() if m.path is None]
        if missing:
            print(f"note: no local checkout for {', '.join(missing)} (set PANZER_MODS_DIR)", file=sys.stderr)
        return [m.path for m in known.values() if m.path is not None]
    if not names:
        names = ["."]
    paths = []
    for name in names:
        path = Path(name)
        if (path / MOD_TOML).is_file():
            paths.append(path.resolve())
        elif name.lower() in known and known[name.lower()].path:
            paths.append(known[name.lower()].path)
        else:
            raise PanzerError(f"'{name}' is neither a mod checkout nor a mod in mods.toml with a local checkout")
    return paths


def select_mods(select: str) -> list:
    """mods.toml entries for a --select value: "all" (or empty), or comma-separated
    keys in any case. Unknown keys are an error rather than an empty selection."""
    known = registry()
    keys = [k.strip().lower() for k in (select or "").split(",") if k.strip()]
    if not keys or keys == ["all"]:
        return known
    unknown = [k for k in keys if k not in {m.key for m in known}]
    if unknown:
        raise PanzerError(f"unknown mod(s) {', '.join(unknown)}; mods.toml has {', '.join(m.key for m in known)}")
    return [m for m in known if m.key in keys]


def git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise PanzerError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


# --------------------------------------------------------------------------- commands


def cmd_sync(args, write: bool) -> int:
    dirty = 0
    for root in resolve_mods(args.mods, args.all):
        mod = load_mod(root)
        plan = panzer_sync.plan(mod)
        label = f"{mod.name} ({root})"
        for note in plan.notes:
            print(f"::warning::{mod.name}: {note}" if args.ci else f"note: {mod.name}: {note}")
        if not plan.changes:
            print(f"{label}: up to date")
            continue
        dirty += 1
        verb = "synced" if write else "out of sync"
        print(f"{label}: {verb}, {len(plan.changes)} file(s)")
        for change in plan.changes:
            print(f"  {change.action:6} {change.path}")
            if args.diff and change.diff():
                print("    " + change.diff().replace("\n", "\n    "))
        if write:
            panzer_sync.apply(plan)
        elif args.ci:
            files = ", ".join(c.path for c in plan.changes)
            print(f"::warning::{mod.name} differs from panzer-build-logic's shared files ({files}). "
                  f"Run `panzer sync` (or the Mods workflow in PanzerBuildLogic) to update them.")
    return 1 if (dirty and not write) else 0


def cmd_mods(args) -> int:
    rows = []
    for m in select_mods(args.select):
        state = "no checkout"
        if m.path:
            try:
                changes = panzer_sync.plan(load_mod(m.path)).changes
                state = "in sync" if not changes else f"{len(changes)} file(s) differ"
            except PanzerError as e:
                state = f"error: {e}"
        rows.append({"key": m.key, "repo": m.repo, "private": m.private, "path": str(m.path or ""), "state": state})
    if args.json:
        print(json.dumps([{"key": r["key"], "repo": r["repo"], "private": r["private"]} for r in rows]))
        return 0
    for r in rows:
        print(f"{r['key']:10} {r['repo']:28} {r['state']:20} {r['path']}")
    return 0


def cmd_new(args) -> int:
    dest = Path(args.dir).resolve()
    if dest.exists() and any(dest.iterdir()):
        raise PanzerError(f"{dest} already exists and is not empty")
    mod_id = args.id
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", mod_id):
        raise PanzerError("--id must be a NeoForge mod id: lowercase letters, digits and _, starting with a letter")
    name = args.name or mod_id.replace("_", " ").title().replace(" ", "")
    package = args.package or f"{args.group}.{mod_id}"
    values = {"id": mod_id, "name": name, "group": args.group, "package": package,
              "package_path": package.replace(".", "/"), "class": name.replace(" ", ""),
              "owner": args.owner, "repo": args.repo or name}
    template = BUILD_LOGIC / "shared" / "new-mod"
    for source in sorted(template.rglob("*")):
        if source.is_dir():
            continue
        rel = str(source.relative_to(template))
        for key, value in values.items():
            rel = rel.replace(f"__{key}__", value)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        text = source.read_text(encoding="utf-8")
        for key, value in values.items():
            text = text.replace("{{" + key + "}}", value)
        target.write_text(text, encoding="utf-8")
    plan = panzer_sync.plan(load_mod(dest))
    panzer_sync.apply(plan)
    print(f"Created {name} ({mod_id}) in {dest} with {len(plan.changes)} shared files.")
    print("Next: git init, add it to panzer-build-logic/mods.toml, create the GitHub repository and push.")
    return 0


def cmd_release(args) -> int:
    for root in resolve_mods(args.mods, False):
        mod = load_mod(root)
        tag = f"v{mod.version}"
        changelog = root / "docs" / "changelogs" / f"{mod.version}.md"
        problems = []
        if not changelog.is_file():
            problems.append(f"{changelog.relative_to(root)} does not exist")
        if git(root, "status", "--porcelain"):
            problems.append("the working tree has uncommitted changes")
        if git(root, "tag", "--list", tag) or git(root, "ls-remote", "--tags", "origin", tag, check=False):
            problems.append(f"tag {tag} already exists")
        drift = panzer_sync.plan(mod).changes
        if drift:
            problems.append(f"shared files are out of sync ({', '.join(c.path for c in drift)}): run `panzer sync`")
        if problems:
            for p in problems:
                print(f"::error::{mod.name}: {p}")
            return 1
        head = git(root, "rev-parse", "--short", "HEAD")
        if not args.push:
            print(f"{mod.name}: ready to tag {tag} at {head}; rerun with --push to tag and push (CI then publishes).")
            continue
        git(root, "tag", "-a", tag, "-m", f"{mod.name} {mod.version}")
        git(root, "push", "origin", tag)
        print(f"{mod.name}: pushed {tag} at {head}; the mod's CI now builds and publishes it.")
    return 0


def cmd_secrets(args) -> int:
    if not shutil.which("gh"):
        raise PanzerError("the GitHub CLI (gh) is required: https://cli.github.com, then `gh auth login`")
    values = {k: os.environ[k] for k in SECRETS if os.environ.get(k)}
    if not values:
        raise PanzerError(f"none of {', '.join(SECRETS)} is set (environment or .env)")
    # PANZER_SYNC_TOKEN can push to every mod, so only panzer-build-logic (the
    # Mods workflow) gets it; the publishing tokens go wherever releases run.
    build_logic = "PanzerDevOrg/PanzerBuildLogic"
    mods = sorted({m.repo for m in registry()})
    plan = []
    for key, value in values.items():
        if key == "PANZER_SYNC_TOKEN":
            plan.append((key, value, ["--repo", build_logic]))
        elif args.org:
            plan.append((key, value, ["--org", args.org, "--visibility", "all"]))
        else:
            plan += [(key, value, ["--repo", r]) for r in mods + [build_logic]]
    for key, value, target in plan:
        print(f"{'(dry run) ' if args.dry_run else ''}gh secret set {key} {' '.join(target)}")
        if not args.dry_run:
            subprocess.run(["gh", "secret", "set", key, *target], input=value, text=True, check=True)
    return 0


def cmd_doctor(args) -> int:
    ok = True
    print(f"panzer-build-logic: {BUILD_LOGIC}")
    print(f"python: {sys.version.split()[0]} {'ok' if sys.version_info >= (3, 11) else '(3.11+ required)'}")
    for tool in ("git", "gh", "java"):
        print(f"{tool}: {shutil.which(tool) or 'not found'}")
    try:
        import markdown_it  # noqa: F401
        print("markdown-it-py: ok")
    except ImportError:
        print("markdown-it-py: missing (pip install markdown-it-py), needed by `panzer publish`")
    for key in SECRETS:
        print(f"{key}: {'set' if os.environ.get(key) else 'not set'}")
    for m in registry():
        print(f"mod {m.key}: {m.path or 'no local checkout'}")
        ok &= m.path is not None
    return 0 if ok else 1


def cmd_token(args) -> int:
    import panzer_token
    raw = os.environ.get("PANZER_SYNC_TOKEN", "")
    if not raw.strip():
        result = panzer_token.Report(["PANZER_SYNC_TOKEN is not set (environment, .env, or the repository secret "
                                      "in CI).", "", "Result: the token is NOT ready (see .env.example)."], False, [])
    else:
        repos = sorted({m.repo for m in select_mods(args.select)})
        result = panzer_token.check(raw, repos, need_write=not args.read_only)
    report = "\n".join(result.lines)
    print(report)
    in_ci = bool(os.environ.get("GITHUB_ACTIONS"))
    if in_ci:
        for w in result.warnings:
            print(f"::warning::{w}")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write("## PANZER_SYNC_TOKEN\n\n" + report + "\n")
    if args.gate and result.usable:
        # Only a token that works nowhere stops the run; a repository it cannot
        # handle is reported here and fails its own job, not everyone's.
        for failure in result.failed:
            print(f"::error::{failure}" if in_ci else f"error: {failure}")
        return 0
    return 0 if result.ok else 1


def cmd_versions(args) -> int:
    import panzer_versions as pv
    values = [v.strip() for item in args.values for v in item.split(",") if v.strip()]
    action = args.action
    if action == "neoforge":
        print("\n".join(pv.neoforge_table(values[0] if values else None)))
        return 0
    if action == "fabric":
        print("\n".join(pv.fabric_table(values[0] if values else None)))
        return 0
    mods = [load_mod(p) for p in resolve_mods(args.mod or [], not args.mod)]
    if action == "list":
        names = [m.name for m in mods]
        print(f"{'version':9} {'minecraft':10} {'neoforge':16} {'java':5} {'downloaded':11}" + "".join(f"{n:10}" for n in names))
        for row in pv.table(mods):
            print(f"{row.version:9} {row.minecraft:10} {row.neoforge:16} {row.java:<5} {'yes' if row.cached else 'no':11}"
                  + "".join(f"{row.mods[n]:10}" for n in names))
        print("\nbuilds: CI and local builds; excluded: not ported yet (-Pstonecutter.versions=<v> still builds it);")
        print("local/skipped: this checkout works on a subset (panzer versions use), the rest is not configured.")
        return 0
    if action == "use":
        if not values:
            raise PanzerError("which versions? e.g. `panzer versions use 1.21.10`")
        for mod in mods:
            print(pv.use(mod, values, switch=not args.no_switch))
        return 0
    if action == "all":
        for mod in mods:
            print(pv.use_all(mod, reset=not args.keep_active))
        return 0
    if action == "prefetch":
        for line in pv.prefetch(mods, values or None):
            print(line)
        return 0
    if action in ("status", "clean"):
        home = pv.gradle_home() / "caches"
        for name in ("neoformruntime", "modules-2"):
            path = home / name
            print(f"{path}: {pv.human(pv.dir_size(path)) if path.is_dir() else 'absent'}")
        cached = pv.cached_neoforge_versions()
        print(f"NeoForge versions downloaded: {', '.join(cached) or 'none'}")
        stale = pv.stale_neoforge()
        if action == "status":
            print(f"Not used by the version matrix: {', '.join(stale) or 'none'}"
                  + (" (panzer versions clean --yes removes them)" if stale else ""))
            return 0
        for line in pv.clean(stale, dry_run=not args.yes) or ["nothing to remove"]:
            print(line)
        if stale and not args.yes:
            print("Dry run: add --yes to remove them.")
        return 0
    raise PanzerError(f"unknown action {action}")


def cmd_compat(args) -> int:
    import panzer_compat
    mod = load_mod(Path(args.mod))
    if args.compat_command == "plan":
        only = [v.strip() for v in args.versions.split(",") if v.strip()] or None
        result = panzer_compat.plan_json(mod, only)
        out = os.environ.get("GITHUB_OUTPUT")
        if args.github_output and out:
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"builds={result}\n")
                client = (mod.config.get("compat") or {}).get("side") == "client"
                f.write(f"client={'true' if client else 'false'}\n")
        print(result)
        return 0
    build = json.loads(args.build)
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    ok, summary = panzer_compat.run_all(mod, build, work)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write("```\n" + "\n".join(summary) + "\n```\n")
    return 0 if ok else 1


def cmd_ci(args) -> int:
    mod = load_mod(Path(args.mod))
    if args.ci_command == "plan":
        result = panzer_ci.plan(mod, args.versions)
        out = os.environ.get("GITHUB_OUTPUT")
        if args.github_output and out:
            with open(out, "a", encoding="utf-8") as f:
                for key, value in result.items():
                    f.write(f"{key}<<__PANZER__\n{value}\n__PANZER__\n")
        print(json.dumps(result, indent=2))
        return 0
    errors, report = panzer_ci.verify_jars(mod, Path(args.libs or mod.root / "build" / "libs"), args.strict)
    for line in report:
        print(f"ok: {line}")
    for e in errors:
        print(f"::error::{e}")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    load_dotenv(Path.cwd(), BUILD_LOGIC)
    if argv[:1] == ["publish"]:
        import panzer_publish
        return panzer_publish.main(argv[1:])

    p = argparse.ArgumentParser(prog="panzer", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("sync", "check"):
        s = sub.add_parser(name, help=f"{name} the shared files of mods")
        s.add_argument("mods", nargs="*")
        s.add_argument("--all", action="store_true", help="every mod in mods.toml with a local checkout")
        s.add_argument("--diff", action="store_true", help="show what changes")
        s.add_argument("--ci", action="store_true", help="GitHub annotations instead of plain notes")
    s = sub.add_parser("mods", help="list the mods")
    s.add_argument("--json", action="store_true")
    s.add_argument("--select", default="all", help="comma-separated keys, or all")
    s = sub.add_parser("new", help="scaffold a new mod")
    s.add_argument("dir")
    s.add_argument("--id", required=True)
    s.add_argument("--name")
    s.add_argument("--group", default="com.panzer.mods")
    s.add_argument("--package")
    s.add_argument("--owner", default="PanzerDevOrg")
    s.add_argument("--repo", help="GitHub repository name (default: the mod name)")
    s = sub.add_parser("release", help="tag a release")
    s.add_argument("mods", nargs="*")
    s.add_argument("--push", action="store_true")
    s = sub.add_parser("secrets", help="push .env tokens to GitHub Actions secrets")
    s.add_argument("--org", help="set organization secrets instead of per-repository ones")
    s.add_argument("--dry-run", action="store_true")
    sub.add_parser("doctor", help="check the local setup")
    s = sub.add_parser("token-check", help="check PANZER_SYNC_TOKEN against every mod repository")
    s.add_argument("--summary", help="also append the report to this file (GitHub step summary)")
    s.add_argument("--select", default="all", help="comma-separated keys from mods.toml, or all")
    s.add_argument("--read-only", action="store_true", help="only reading is needed (e.g. to build)")
    s.add_argument("--gate", action="store_true",
                   help="fail only if the token works for no selected repository (CI pre-check)")
    sub.add_parser("publish", help="release publisher (see publishing/README.md)", add_help=False)
    s = sub.add_parser("versions", help="Minecraft versions and their local cache")
    s.add_argument("action", nargs="?", default="list",
                   choices=["list", "use", "all", "prefetch", "status", "clean", "neoforge", "fabric"])
    s.add_argument("values", nargs="*", help="versions (use, prefetch; neoforge: oldest Minecraft to list, default 1.21)")
    s.add_argument("--mod", action="append", help="mod path or key (repeatable; default every local checkout)")
    s.add_argument("--no-switch", action="store_true", help="use: keep Stonecutter's active version")
    s.add_argument("--keep-active", action="store_true", help="all: do not reset Stonecutter's active version")
    s.add_argument("--yes", action="store_true", help="clean: really remove")
    s = sub.add_parser("compat", help="run a built jar on the other Minecraft versions it claims (game_versions)")
    s.add_argument("compat_command", choices=["plan", "run"])
    s.add_argument("--mod", default=".")
    s.add_argument("--versions", default="", help="plan: only these build versions")
    s.add_argument("--github-output", action="store_true")
    s.add_argument("--build", help="run: one entry of the plan (JSON)")
    s.add_argument("--work", default="build/compat", help="run: where servers are installed")
    s.add_argument("--summary", help="run: append the results to this file (GitHub step summary)")
    s = sub.add_parser("ci", help="CI helpers")
    s.add_argument("ci_command", choices=["plan", "verify-jars"])
    s.add_argument("--mod", default=".")
    s.add_argument("--github-output", action="store_true")
    s.add_argument("--libs")
    s.add_argument("--strict", action="store_true")
    s.add_argument("--versions", default="", help="plan: build only these versions (may be excluded ones)")
    args = p.parse_args(argv)

    try:
        if args.command in ("sync", "check"):
            return cmd_sync(args, write=args.command == "sync")
        return {"mods": cmd_mods, "new": cmd_new, "release": cmd_release, "secrets": cmd_secrets,
                "doctor": cmd_doctor, "token-check": cmd_token, "versions": cmd_versions, "compat": cmd_compat,
                "ci": cmd_ci}[args.command](args)
    except PanzerError as e:
        # 2, not 1: `check` uses 1 for "files differ", and CI tells them apart.
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 - a crash is an error, never "files differ"
        import traceback
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
