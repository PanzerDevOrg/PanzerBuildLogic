"""Shared files: what panzer-build-logic keeps identical in every mod.

shared/manifest.toml lists them. `panzer sync` writes them into a mod
checkout, `panzer check` reports what differs (CI fails or warns on it), and
.github/workflows/mods.yml pushes them to every repository in mods.toml.
"""
from __future__ import annotations

import dataclasses
import difflib
import os
import re
import stat
from pathlib import Path

from panzer_legal import Legal
from panzer_mod import BUILD_LOGIC, Mod, PanzerError, read_toml

GITIGNORE_BEGIN = "# >>> panzer-build-logic: managed by `panzer sync`, add your own entries below the block"
GITIGNORE_END = "# <<< panzer-build-logic"


@dataclasses.dataclass
class Change:
    path: str
    action: str  # "create" / "update" / "delete"
    content: bytes | None = None
    executable: bool = False
    old: bytes | None = None

    def diff(self, limit: int = 40) -> str:
        if self.content is None or self.old is None or b"\0" in (self.content + self.old):
            return ""
        lines = list(difflib.unified_diff(self.old.decode("utf-8", "replace").splitlines(),
                                          self.content.decode("utf-8", "replace").splitlines(),
                                          f"a/{self.path}", f"b/{self.path}", lineterm="", n=1))
        if len(lines) > limit:
            lines = lines[:limit] + [f"... ({len(lines) - limit} more diff lines)"]
        return "\n".join(lines)


@dataclasses.dataclass
class SyncPlan:
    mod: Mod
    changes: list[Change]
    notes: list[str]


def manifest(build_logic: Path = BUILD_LOGIC) -> dict:
    return read_toml(build_logic / "shared" / "manifest.toml")


def _read(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def keep_lines(template: str, current: str | None, patterns: list[str]) -> str:
    """Lines of `template` matching one of `patterns` take the mod's current
    line matching the same pattern (e.g. `stonecutter active "<version>"`)."""
    if not current:
        return template
    out = []
    for line in template.splitlines(keepends=True):
        for pattern in patterns:
            if re.match(pattern, line):
                mine = next((l for l in current.splitlines(keepends=True) if re.match(pattern, l)), None)
                if mine is not None:
                    line = mine if mine.endswith("\n") or not line.endswith("\n") else mine + "\n"
                break
        out.append(line)
    return "".join(out)


def gitignore(current: str | None, managed: list[str]) -> str:
    block = "\n".join([GITIGNORE_BEGIN, *managed, GITIGNORE_END])
    text = current or ""
    if GITIGNORE_BEGIN.split(":")[0] in text and GITIGNORE_END in text:
        start = text.index(GITIGNORE_BEGIN.split(":")[0])
        end = text.index(GITIGNORE_END) + len(GITIGNORE_END)
        rest = text[:start] + text[end:]
    else:
        rest = text
    entries = {line.strip() for line in managed if line.strip() and not line.startswith("#")}
    lines = rest.splitlines()
    kept = [line for line in lines if line.strip() not in entries]
    # A comment that only introduced entries now in the block goes too.
    removed_any = len(kept) != len(lines)
    if removed_any:
        cleaned = []
        for i, line in enumerate(kept):
            following = next((l for l in kept[i + 1:] if not l.strip().startswith("#")), "")
            if line.strip().startswith("#") and not following.strip():
                continue
            cleaned.append(line)
        kept = cleaned
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip("\n")
    return block + "\n" + ("\n" + body + "\n" if body else "")


def readme_blocks(current: str, blocks: dict[str, str]) -> tuple[str, list[str]]:
    missing = []
    for name, content in blocks.items():
        pattern = re.compile(rf"<!--\s*panzer:{name}\s*-->.*?<!--\s*/panzer:{name}\s*-->", re.S)
        if not pattern.search(current):
            missing.append(name)
            continue
        replacement = f"<!-- panzer:{name} -->\n{content}\n<!-- /panzer:{name} -->"
        current = pattern.sub(lambda _: replacement, current, count=1)
    return current, missing


def desired_files(mod: Mod, build_logic: Path = BUILD_LOGIC) -> tuple[dict[str, tuple[bytes | None, bool]], list[str]]:
    """Path -> (content or None to delete, executable) for one mod, plus notes."""
    spec = manifest(build_logic)
    root = mod.root
    out: dict[str, tuple[bytes | None, bool]] = {}
    notes: list[str] = []

    for entry in spec.get("file", []):
        path = entry["path"]
        source = build_logic / entry.get("from", f"shared/mod/{path}")
        if not source.is_file():
            raise PanzerError(f"shared/manifest.toml: source {source} of {path} does not exist")
        data = source.read_bytes()
        if entry.get("keep"):
            current = _read(root / path)
            data = keep_lines(data.decode("utf-8"), current.decode("utf-8") if current else None,
                              entry["keep"]).encode("utf-8")
        out[path] = (data, bool(entry.get("executable")))

    if spec.get("gitignore", {}).get("lines"):
        current = _read(root / ".gitignore")
        text = gitignore(current.decode("utf-8") if current else None, spec["gitignore"]["lines"])
        out[".gitignore"] = (text.encode("utf-8"), False)

    legal = Legal(mod, build_logic)
    for path, text in legal.files().items():
        out[path] = (text.encode("utf-8") if text is not None else None, False)

    readme = _read(root / "README.md")
    names = spec.get("readme", {}).get("blocks", [])
    if readme is not None and names:
        blocks = {k: v for k, v in legal.readme_blocks().items() if k in names}
        text, missing = readme_blocks(readme.decode("utf-8"), blocks)
        out["README.md"] = (text.encode("utf-8"), False)
        for name in missing:
            notes.append(f"README.md has no <!-- panzer:{name} --> ... <!-- /panzer:{name} --> block; "
                         f"add one where the {name} section goes to have it kept up to date")

    for path in spec.get("remove", {}).get("paths", []):
        out.setdefault(path, (None, False))
    return out, notes


def plan(mod: Mod, build_logic: Path = BUILD_LOGIC) -> SyncPlan:
    files, notes = desired_files(mod, build_logic)
    changes = []
    for path, (content, executable) in sorted(files.items()):
        target = mod.root / path
        old = _read(target)
        if content is None:
            if old is not None:
                changes.append(Change(path, "delete", old=old))
            continue
        exec_differs = executable and os.name != "nt" and target.is_file() and not os.access(target, os.X_OK)
        if old != content or exec_differs:
            changes.append(Change(path, "create" if old is None else "update", content, executable, old))
    return SyncPlan(mod, changes, notes)


def apply(sync: SyncPlan) -> None:
    for change in sync.changes:
        target = sync.mod.root / change.path
        if change.action == "delete":
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(change.content)
        if change.executable and os.name != "nt":
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
