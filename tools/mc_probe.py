#!/usr/bin/env python3
"""Answers questions about one Minecraft/NeoForge version from its decompiled
sources, for porting work done away from an IDE (run by a mod's private
mc-probe workflow; the output is the job log). --sources and --binary may be
given several times (Minecraft's patched sources, NeoForge's own sources...).

Queries, one per line (or separated by ';;'):
  class  <fqn>                 the whole source file
  method <fqn>#<name>          every declaration of <name> in that class, with its body
  grep   <regex> [<path-prefix>]   matching lines (path:line: text), at most 300
  find   <regex>               source paths matching the regex
  sig    <fqn>                 javap -p of the compiled class (members and descriptors)
  file   <path>                any file from the sources or binary jars (shaders, JSON...)
  neoforge [<minecraft>]       NeoForge builds published per Minecraft version (panzer versions neoforge)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import zipfile

MAX_GREP = 300


class Sources:
    """Several sources jars read as one; the first jar holding a path wins."""

    def __init__(self, jars: list[zipfile.ZipFile]):
        self.owner: dict[str, zipfile.ZipFile] = {}
        for jar in jars:
            for name in jar.namelist():
                self.owner.setdefault(name, jar)
        self.names = set(self.owner)

    def read(self, path: str) -> bytes:
        return self.owner[path].read(path)


def source_path(fqn: str) -> str:
    return fqn.split("$")[0].replace(".", "/") + ".java"


def methods(text: str, name: str) -> list[tuple[int, list[str]]]:
    """(first line number, lines) of each declaration of `name` with its body."""
    lines = text.splitlines()
    # Modifiers and annotations in any order (type-use ones like `public @Nullable
    # BlockState` included), optional type parameters, the return type, the name.
    decl = re.compile(rf"^\s*(?:(?:@[\w.]+(?:\([^)]*\))?|public|protected|private|static|final|abstract|"
                      rf"synchronized|native|default)\s+)*(?:<[^>]+>\s+)?(?:@[\w.]+\s+)*[\w.<>\[\], ?]+\s+"
                      rf"{re.escape(name)}\s*\(")
    found = []
    i = 0
    while i < len(lines):
        if decl.match(lines[i]) and not lines[i].strip().startswith(("return", "new ", "if", "for", "while")):
            start = i
            # include annotations and javadoc directly above
            while start > 0 and lines[start - 1].strip().startswith(("@", "*", "/**", "//")):
                start -= 1
            depth, j, opened = 0, i, False
            while j < len(lines):
                code = re.sub(r'"(?:\\.|[^"\\])*"', '""', lines[j])
                code = code.split("//")[0]
                depth += code.count("{") - code.count("}")
                opened |= "{" in code
                if (opened and depth <= 0) or (not opened and code.rstrip().endswith(";")):
                    break
                j += 1
            found.append((start + 1, lines[start:j + 1]))
            i = j + 1
        else:
            i += 1
    return found


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sources", required=True, action="append", help="sources jar (repeatable)")
    p.add_argument("--binary", required=True, action="append", help="class jar for sig queries (repeatable)")
    p.add_argument("--query", required=True, help="queries, newline or ';;' separated")
    args = p.parse_args()
    src = Sources([zipfile.ZipFile(path) for path in args.sources])
    names = src.names
    everything = Sources([zipfile.ZipFile(path) for path in args.sources + args.binary])
    queries = [q.strip() for q in re.split(r"\n|;;", args.query) if q.strip()]
    for q in queries:
        kind, _, rest = q.partition(" ")
        rest = rest.strip()
        print(f"\n===== {q}")
        try:
            if kind == "class":
                path = source_path(rest)
                print(src.read(path).decode() if path in names else f"(no {path})")
            elif kind == "method":
                cls, _, name = rest.partition("#")
                path = source_path(cls)
                if path not in names:
                    print(f"(no {path})")
                    continue
                hits = methods(src.read(path).decode(), name)
                for line_no, body in hits:
                    print(f"--- {path}:{line_no}")
                    print("\n".join(body))
                if not hits:
                    print(f"(no declaration of {name} in {path})")
            elif kind == "grep":
                regex, _, prefix = rest.partition(" ")
                pattern, count = re.compile(regex), 0
                for path in sorted(n for n in names if n.endswith(".java") and n.startswith(prefix.strip())):
                    for no, line in enumerate(src.read(path).decode(errors="replace").splitlines(), 1):
                        if pattern.search(line):
                            print(f"{path}:{no}: {line.strip()}")
                            count += 1
                            if count >= MAX_GREP:
                                break
                    if count >= MAX_GREP:
                        print(f"(stopped at {MAX_GREP} matches)")
                        break
            elif kind == "find":
                pattern = re.compile(rest)
                for path in sorted(n for n in names if pattern.search(n)):
                    print(path)
            elif kind == "file":
                if rest in everything.names:
                    print(everything.read(rest).decode(errors="replace"))
                else:
                    matches = sorted(n for n in everything.names if n.endswith("/" + rest.split("/")[-1]))
                    print(f"(no {rest}" + (f"; same name: {', '.join(matches[:10])})" if matches else ")"))
            elif kind == "sig":
                out = subprocess.run(["javap", "-p", "-cp", ":".join(args.binary), rest], capture_output=True, text=True)
                print(out.stdout or out.stderr)
            elif kind == "neoforge":
                import panzer_versions
                print("\n".join(panzer_versions.neoforge_table(rest or None)))
            else:
                print(f"(unknown query kind '{kind}')")
        except Exception as e:  # noqa: BLE001 - one bad query must not hide the others
            print(f"(error: {type(e).__name__}: {e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
