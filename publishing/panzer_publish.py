#!/usr/bin/env python3
"""Release and description publishing for PanzerDevOrg mods.

One tool, three destinations, driven by the mod's own files:

  README.md                         the single description, for GitHub, Modrinth and CurseForge
  docs/changelogs/<version>.md      the changelog of each release
  mod.stonecutter.properties.toml   [mod], [stonecutter], per-build game_versions, [publish]
  build/libs/<version>/*.jar        what CI built (universal, per-OS and sources jars)

Commands (all write only under --out unless they publish):

  describe   README -> modrinth.md + curseforge.html (+ lint warnings)
  plan       everything above + jars -> plan.json (exactly what would be uploaded where)
  check      validate a plan (files, changelog, tag, game versions known to each site)
  preview    render plan.json as local HTML pages: Modrinth page, CurseForge page,
             GitHub release, upload table. Nothing leaves the machine.
  publish    upload a plan: --targets github,modrinth,curseforge (each one optional)
  sync       push the description to Modrinth (CurseForge has no description API:
             its HTML is written for pasting, see README)

Only the standard library is required, plus markdown-it-py for the HTML rendering
(`pip install markdown-it-py`). Tokens come from the environment or a .env file
(the current directory, the mod, panzer-build-logic; see .env.example):
MODRINTH_TOKEN, CURSEFORGE_TOKEN, GH_TOKEN (for `gh`). Also available as
`panzer publish ...`.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
import uuid
from pathlib import Path

MODRINTH_API = "https://api.modrinth.com/v2"
CURSEFORGE_API = "https://minecraft.curseforge.com/api"
USER_AGENT = "PanzerDevOrg/panzer-publish (github.com/PanzerDevOrg/PanzerBuildLogic)"

SIDES = ("required", "optional", "unsupported")
OS_LABELS = {
    "windows": "Windows",
    "linux": "Linux",
    "macos": "macOS",
    "java": "Pure Java (no native libraries)",
}


class PublishError(Exception):
    pass


# --------------------------------------------------------------------------- config


@dataclasses.dataclass
class Dependency:
    mod_id: str
    type: str  # required / optional / incompatible / embedded
    modrinth: str | None
    curseforge: str | None  # project slug (the CurseForge upload API relates by slug)
    github: str | None  # Owner/Repo: README links to it are pointed at the dependency's page on each site


@dataclasses.dataclass
class Build:
    name: str  # Stonecutter build, e.g. "1.21.1"
    game_versions: list[str]
    java: int


@dataclasses.dataclass
class ModConfig:
    root: Path
    mod_id: str
    name: str
    version: str
    side: str
    github: str
    modrinth_id: str | None
    curseforge_id: str | None
    curseforge_slug: str | None
    client_side: str
    server_side: str
    platforms: list[str]
    dependencies: list[Dependency]
    builds: list[Build]
    branch: str
    source_url: str = ""
    issues_url: str = ""

    @property
    def release_type(self) -> str:
        v = self.version.lower()
        if "alpha" in v:
            return "alpha"
        if "beta" in v or "rc" in v or "pre" in v:
            return "beta"
        return "release"

    @property
    def curseforge_environments(self) -> list[str]:
        envs = []
        if self.client_side != "unsupported":
            envs.append("Client")
        if self.server_side != "unsupported":
            envs.append("Server")
        return envs


def java_for(minecraft: str) -> int:
    """Same rule as panzer-build-logic's ModBuildProperties.requiredJava."""
    parts = [int(p) for p in re.findall(r"\d+", minecraft)[:3]]
    if parts and parts[0] >= 26:
        return 25
    return 21


def read_mod_toml(root: Path) -> tuple[dict, list[str]]:
    """The mod's configuration merged with panzer-build-logic's common TOML (the
    same merge the build does) and the Minecraft builds it declares."""
    toml_path = root / "mod.stonecutter.properties.toml"
    if not toml_path.exists():
        raise PublishError(f"{toml_path} not found")
    tools = Path(__file__).resolve().parent.parent / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    try:
        import panzer_mod
    except ImportError:  # publisher used on its own: no common TOML to merge
        with toml_path.open("rb") as f:
            data = tomllib.load(f)
        return data, list((data.get("stonecutter") or {}).get("versions", []))
    try:
        mod = panzer_mod.load_mod(root)
    except panzer_mod.PanzerError as e:
        raise PublishError(str(e)) from e
    return mod.config, mod.stonecutter_versions


def load_config(root: Path) -> ModConfig:
    toml_path = root / "mod.stonecutter.properties.toml"
    data, versions = read_mod_toml(root)
    mod = data.get("mod") or {}
    publish = data.get("publish") or {}
    for key in ("id", "name", "version"):
        if key not in mod:
            raise PublishError(f"[mod] {key} is missing in {toml_path}")

    side = str(mod.get("side", "BOTH")).upper()
    default_client = "unsupported" if side == "SERVER" else "required"
    default_server = "unsupported" if side == "CLIENT" else "required"
    client_side = publish.get("client_side", default_client)
    server_side = publish.get("server_side", default_server)
    for label, value in (("client_side", client_side), ("server_side", server_side)):
        if value not in SIDES:
            raise PublishError(f"[publish] {label} must be one of {SIDES}, not {value!r}")

    github = publish.get("github")
    if not github:
        issues = str(mod.get("issues", ""))
        m = re.match(r"https://github\.com/([^/]+/[^/]+)", issues)
        if not m:
            raise PublishError("[publish] github = \"Owner/Repo\" is missing (and [mod] issues is not a GitHub URL)")
        github = m.group(1)

    platforms = list(publish.get("platforms", []))
    unknown = [p for p in platforms if p not in OS_LABELS]
    if unknown:
        raise PublishError(f"[publish] platforms {unknown} unknown; use any of {list(OS_LABELS)}")

    dependencies = []
    for dep_id, dep in (publish.get("dependencies") or {}).items():
        dtype = dep.get("type", "required")
        if dtype not in ("required", "optional", "incompatible", "embedded"):
            raise PublishError(f"[publish.dependencies.{dep_id}] type {dtype!r} unknown")
        dependencies.append(Dependency(dep_id, dtype, dep.get("modrinth"), dep.get("curseforge"), dep.get("github")))

    builds = []
    for build in versions:
        block = data.get(build) or {}
        gv = list(block.get("game_versions") or [block.get("minecraft_version", build)])
        lower = str(block.get("minecraft_version_range", "")).strip("[(").split(",")[0]
        if lower and lower != gv[0]:
            raise PublishError(f'["{build}"] minecraft_version_range starts at {lower} but game_versions at {gv[0]}')
        if block.get("minecraft_version", build) not in gv:
            raise PublishError(f'["{build}"] builds against {block.get("minecraft_version", build)}, which is not in game_versions {gv}')
        builds.append(Build(build, gv, int(block.get("java", java_for(build)))))
    if not builds:
        raise PublishError("[stonecutter] versions is empty")

    return ModConfig(
        root=root,
        mod_id=mod["id"],
        name=mod["name"],
        version=str(mod["version"]),
        side=side,
        github=github,
        modrinth_id=os.environ.get("MODRINTH_ID") or publish.get("modrinth_id") or None,
        curseforge_id=os.environ.get("CURSEFORGE_ID") or (str(publish["curseforge_id"]) if publish.get("curseforge_id") else None),
        curseforge_slug=publish.get("curseforge_slug"),
        client_side=client_side,
        server_side=server_side,
        platforms=platforms,
        dependencies=dependencies,
        builds=builds,
        branch=publish.get("branch", "master"),
        # Project links on Modrinth; "" leaves one out (a private repository has none to show).
        source_url=str(publish.get("source_url", f"https://github.com/{github}")),
        issues_url=str(publish.get("issues_url", mod.get("issues", ""))),
    )


# --------------------------------------------------------------------------- versions


def range_label(versions: list[str]) -> str:
    """1.21 .. 1.21.6 -> "1.21(.0-.6)"; a single version stays as is (the published scheme)."""
    if len(versions) == 1:
        return versions[0]
    first, last = versions[0].split("."), versions[-1].split(".")
    width = max(len(first), len(last))
    first += ["0"] * (width - len(first))
    last += ["0"] * (width - len(last))
    common = []
    for a, b in zip(first[:-1], last[:-1]):
        if a != b:
            break
        common.append(a)
    head = ".".join(common)
    return f"{head}(.{'.'.join(first[len(common):])}-.{'.'.join(last[len(common):])})"


def range_text(versions: list[str]) -> str:
    """Human form for display names: "1.21.1", "1.21–1.21.6"."""
    return versions[0] if len(versions) == 1 else f"{versions[0]}–{versions[-1]}"


# --------------------------------------------------------------------------- description

PUBLISH_OFF = re.compile(r"<!--\s*publish:off\s*-->.*?<!--\s*publish:on\s*-->\n?", re.S)
# <!-- panzer:<block> --> markers around README blocks `panzer sync` maintains.
PANZER_MARKER = re.compile(r"[ \t]*<!--\s*/?panzer:[\w-]+\s*-->[ \t]*\n?")
MD_LINK = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)((?:\s+\"[^\"]*\")?)\)")
HTML_SRC = re.compile(r"""(<img\b[^>]*?\bsrc=)(["'])([^"']+)\2""", re.I)
HTML_HREF = re.compile(r"""(<a\b[^>]*?\bhref=)(["'])([^"']+)\2""", re.I)


def is_absolute(url: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I)) or url.startswith("#") or url.startswith("//")


def absolutize(url: str, cfg: ModConfig, image: bool) -> str:
    if is_absolute(url):
        return url
    path = url[2:] if url.startswith("./") else url.lstrip("/")
    if image:
        return f"https://raw.githubusercontent.com/{cfg.github}/{cfg.branch}/{path}"
    return f"https://github.com/{cfg.github}/blob/{cfg.branch}/{path}"


def site_url(url: str, cfg: ModConfig, site: str) -> str:
    """Links to a dependency's GitHub repo become its page on the site being published to:
    CurseForge does not allow links to other download platforms, and players on each
    site should land on that site's page."""
    for dep in cfg.dependencies:
        if not dep.github or url.rstrip("/") != f"https://github.com/{dep.github}":
            continue
        if site == "modrinth" and dep.modrinth:
            return f"https://modrinth.com/mod/{dep.modrinth}"
        if site == "curseforge" and dep.curseforge:
            return f"https://www.curseforge.com/minecraft/mc-mods/{dep.curseforge}"
    return url


def published_markdown(readme: str, cfg: ModConfig, site: str = "modrinth") -> str:
    """The README as a mod site gets it: GitHub-only blocks removed, links made absolute,
    dependency links pointed at that site."""
    text = PANZER_MARKER.sub("", PUBLISH_OFF.sub("", readme))

    def md(m: re.Match) -> str:
        bang, label, url, title = m.groups()
        url = absolutize(url, cfg, bool(bang))
        return f"{bang}[{label}]({url if bang else site_url(url, cfg, site)}{title})"

    text = MD_LINK.sub(md, text)
    text = HTML_SRC.sub(lambda m: f"{m.group(1)}{m.group(2)}{absolutize(m.group(3), cfg, True)}{m.group(2)}", text)
    text = HTML_HREF.sub(lambda m: f"{m.group(1)}{m.group(2)}{site_url(absolutize(m.group(3), cfg, False), cfg, site)}{m.group(2)}", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def markdown_to_html(markdown: str) -> str:
    try:
        from markdown_it import MarkdownIt
    except ImportError as e:
        raise PublishError("markdown-it-py is required for HTML rendering: pip install markdown-it-py") from e
    md = MarkdownIt("commonmark", {"html": True, "linkify": False, "typographer": False}).enable(["table", "strikethrough"])
    return md.render(markdown)


# Tags and attributes CurseForge's description editor keeps (its HTML/"Source" mode).
CF_ALLOWED = {
    "a": {"href", "title"}, "img": {"src", "alt", "title", "width", "height"}, "p": set(), "br": set(), "hr": set(),
    "h1": set(), "h2": set(), "h3": set(), "h4": set(), "h5": set(), "h6": set(), "strong": set(), "em": set(),
    "del": set(), "code": set(), "pre": set(), "blockquote": set(), "ul": set(), "ol": set(), "li": set(),
    "table": set(), "thead": set(), "tbody": set(), "tr": set(), "th": {"style"}, "td": {"style"},
}


def curseforge_html(markdown: str) -> str:
    """CurseForge-ready HTML: rendered markdown, reduced to what CurseForge keeps,
    with layout-only wrappers (e.g. <div align="center">) unwrapped and table
    alignment expressed as inline text-align (which CurseForge honours)."""
    rendered = markdown_to_html(markdown)

    def tag(m: re.Match) -> str:
        closing, name, attrs = m.group(1), m.group(2).lower(), m.group(3) or ""
        if name not in CF_ALLOWED:
            return ""
        if closing:
            return f"</{name}>"
        kept = []
        for an, av in re.findall(r"""([a-zA-Z-]+)\s*=\s*("[^"]*"|'[^']*')""", attrs):
            an = an.lower()
            if an in CF_ALLOWED[name]:
                if an == "style" and not re.fullmatch(r"""["']text-align:\s*(left|center|right);?["']""", av):
                    continue
                kept.append(f"{an}={av}")
        if name == "a":
            kept.append('rel="noopener nofollow"')
        self_closing = "/" if name in ("img", "br", "hr") else ""
        return f"<{name}{''.join(' ' + k for k in kept)}{self_closing}>"

    cleaned = re.sub(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)([^>]*?)/?>", tag, rendered)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip() + "\n"


def lint_readme(readme: str) -> list[str]:
    """Things that render differently (or not at all) on one of the sites."""
    warnings = []
    published = PUBLISH_OFF.sub("", readme)
    # Code is literal on every site: `<version>` or a fenced block is not HTML.
    published = re.sub(r"```.*?```", "", published, flags=re.S)
    published = re.sub(r"`[^`\n]*`", "", published)
    if re.search(r"\sstyle\s*=", published):
        warnings.append("style= attributes are dropped by GitHub and Modrinth; use markdown or align/text-align only")
    for tag_name in sorted(set(re.findall(r"<(iframe|script|video|audio|svg|picture|source|details|summary|center|font|span)\b", published, re.I))):
        warnings.append(f"<{tag_name}> does not render on every site (CurseForge strips it)")
    if re.search(r"^\s*\|.*<[a-zA-Z][^>]*>.*\|\s*$", published, re.M):
        warnings.append("HTML inside table rows renders inconsistently; keep table cells plain markdown")
    if re.search(r"^#{5,}\s", published, re.M):
        warnings.append("headings deeper than #### look identical to body text on CurseForge")
    if "<!-- publish:off -->" in readme and "<!-- publish:on -->" not in readme:
        warnings.append("<!-- publish:off --> without a matching <!-- publish:on -->: the rest of the README is GitHub-only")
    return warnings


# --------------------------------------------------------------------------- plan


def find_jars(dist: Path, cfg: ModConfig) -> dict[str, dict[str, Path]]:
    """{build: {"universal": jar, "sources": jar, "<os>": jar}} from the collected jars."""
    found: dict[str, dict[str, Path]] = {}
    pattern = re.compile(rf"^{re.escape(cfg.mod_id)}-{re.escape(cfg.version)}\+(?P<build>[0-9][^-]*)(?:-(?P<cls>[a-z0-9_]+))?\.jar$")
    for jar in sorted(dist.rglob("*.jar")):
        m = pattern.match(jar.name)
        if not m:
            continue
        cls = m.group("cls") or "universal"
        if cls == "dev":
            continue
        found.setdefault(m.group("build"), {})[cls] = jar
    return found


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_plan(cfg: ModConfig, dist: Path, tag: str | None) -> dict:
    readme = (cfg.root / "README.md").read_text(encoding="utf-8")
    changelog_path = cfg.root / "docs" / "changelogs" / f"{cfg.version}.md"
    changelog = changelog_path.read_text(encoding="utf-8").strip() + "\n" if changelog_path.exists() else ""
    jars = find_jars(dist, cfg) if dist and dist.exists() else {}

    files = []
    for build in cfg.builds:
        build_jars = jars.get(build.name, {})
        label = range_label(build.game_versions)
        display = f"{cfg.version} {range_text(build.game_versions)}"  # e.g. "0.2.2 1.21–1.21.6"
        universal = build_jars.get("universal")
        entry = {
            "build": build.name,
            "game_versions": build.game_versions,
            "java": build.java,
            "version_number": f"{cfg.version}-{label}",
            "display_name": display,
            "universal": str(universal) if universal else None,
            "universal_sha256": sha256(universal) if universal else None,
            "sources": str(build_jars["sources"]) if "sources" in build_jars else None,
            "platforms": {os_: str(build_jars[os_]) for os_ in cfg.platforms if os_ in build_jars},
        }
        files.append(entry)

    plan = {
        "mod": {"id": cfg.mod_id, "name": cfg.name, "version": cfg.version, "github": cfg.github,
                "release_type": cfg.release_type, "side": cfg.side,
                "client_side": cfg.client_side, "server_side": cfg.server_side},
        "tag": tag,
        "changelog": changelog,
        "changelog_path": str(changelog_path),
        "readme_warnings": lint_readme(readme),
        "modrinth": {
            "project": cfg.modrinth_id,
            "loaders": ["neoforge"],
            "dependencies": [{"project_id": d.modrinth, "dependency_type": d.type}
                             for d in cfg.dependencies if d.modrinth],
        },
        "curseforge": {
            "project": cfg.curseforge_id,
            "slug": cfg.curseforge_slug,
            "loader": "NeoForge",
            "environments": cfg.curseforge_environments,
            "relations": [{"slug": d.curseforge, "type": {"required": "requiredDependency",
                                                          "optional": "optionalDependency",
                                                          "incompatible": "incompatible",
                                                          "embedded": "embeddedLibrary"}[d.type]}
                          for d in cfg.dependencies if d.curseforge],
        },
        "files": files,
    }
    return plan


def check_plan(plan: dict, online: bool) -> list[str]:
    errors = []
    mod = plan["mod"]
    if plan.get("tag") and plan["tag"] != f"v{mod['version']}":
        errors.append(f"tag {plan['tag']} does not match [mod] version {mod['version']} (expected v{mod['version']})")
    if not plan["changelog"]:
        errors.append(f"missing changelog {plan['changelog_path']}")
    for f in plan["files"]:
        if not f["universal"]:
            errors.append(f"no jar built for {f['build']}")
    names = [f["version_number"] for f in plan["files"]]
    if len(set(names)) != len(names):
        errors.append(f"duplicate version numbers {names}: give each build distinct game_versions")
    if online:
        if plan["modrinth"]["project"]:
            try:
                known = {t["version"] for t in http_json("GET", f"{MODRINTH_API}/tag/game_version")}
                for f in plan["files"]:
                    missing = [v for v in f["game_versions"] if v not in known]
                    if missing:
                        errors.append(f"Modrinth does not know game versions {missing} ({f['build']})")
            except PublishError as e:
                errors.append(f"could not list Modrinth game versions: {e}")
        if plan["curseforge"]["project"] and os.environ.get("CURSEFORGE_TOKEN"):
            try:
                cf = CurseForgeVersions.fetch(os.environ["CURSEFORGE_TOKEN"])
                for f in plan["files"]:
                    cf.ids(f, plan, errors)
            except PublishError as e:
                errors.append(f"could not list CurseForge game versions: {e}")
    return errors


# --------------------------------------------------------------------------- http


def http_json(method: str, url: str, token: str | None = None, body: dict | None = None,
              headers: dict | None = None) -> object:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", USER_AGENT)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if token:
        req.add_header("Authorization", token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise PublishError(f"{method} {url} -> HTTP {e.code}: {e.read().decode(errors='replace')[:500]}") from e
    except urllib.error.URLError as e:
        raise PublishError(f"{method} {url} -> {e.reason}") from e


def http_multipart(url: str, fields: dict[str, str], files: dict[str, Path], headers: dict) -> object:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n"
                     f"Content-Type: application/json\r\n\r\n{value}\r\n".encode())
    for name, path in files.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{path.name}\"\r\n"
                     f"Content-Type: application/java-archive\r\n\r\n".encode() + path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(url, data=b"".join(parts), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("User-Agent", USER_AGENT)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise PublishError(f"POST {url} -> HTTP {e.code}: {e.read().decode(errors='replace')[:500]}") from e


# --------------------------------------------------------------------------- curseforge


class CurseForgeVersions:
    """Name -> id resolution for CurseForge's "game versions" (MC versions, loaders,
    Java and environment tags all live in the same list). Unknown names are errors:
    the old pipeline silently dropped them, leaving files without versions."""

    def __init__(self, versions: list[dict], types: list[dict]):
        self.types = {t["id"]: t["slug"] for t in types}
        self.versions = versions

    @classmethod
    def fetch(cls, token: str) -> "CurseForgeVersions":
        h = {"X-Api-Token": token}
        return cls(http_json("GET", f"{CURSEFORGE_API}/game/versions", headers=h),
                   http_json("GET", f"{CURSEFORGE_API}/game/version-types", headers=h))

    def find(self, name: str, type_prefix: str) -> int | None:
        for v in self.versions:
            if v["name"] == name and self.types.get(v["gameVersionTypeID"], "").startswith(type_prefix):
                return v["id"]
        return None

    def ids(self, file: dict, plan: dict, errors: list[str]) -> list[int]:
        out = []
        wanted = [(v, "minecraft") for v in file["game_versions"]]
        wanted.append((plan["curseforge"]["loader"], "modloader"))
        wanted.append((f"Java {file['java']}", "java"))
        wanted += [(e, "environment") for e in plan["curseforge"]["environments"]]
        for name, prefix in wanted:
            vid = self.find(name, prefix)
            if vid is None:
                errors.append(f"CurseForge has no {prefix} version {name!r} ({file['build']})")
            else:
                out.append(vid)
        return out


# --------------------------------------------------------------------------- publish


def publish_github(plan: dict, dry: bool, log, out: Path) -> None:
    tag = plan["tag"]
    if not tag:
        raise PublishError("GitHub release needs --tag")
    notes = github_release_body(plan)
    notes_file = out / "github-release.md"
    notes_file.write_text(notes, encoding="utf-8")
    assets = github_assets(plan)
    sums = out / "SHA256SUMS.txt"
    sums.write_text("".join(f"{sha256(Path(a))}  {Path(a).name}\n" for a in assets), encoding="utf-8")
    assets.append(str(sums))
    title = f"{plan['mod']['name']} {plan['mod']['version']}"
    exists = subprocess.run(["gh", "release", "view", tag], capture_output=True).returncode == 0 if not dry else False
    if exists:
        cmd = ["gh", "release", "upload", tag, "--clobber", *assets]
    else:
        cmd = ["gh", "release", "create", tag, "--title", title, "--notes-file", str(notes_file), "--verify-tag", *assets]
        if plan["mod"]["release_type"] != "release":
            cmd.append("--prerelease")
    log(f"github: {' '.join(cmd[:4])} ... ({len(assets)} assets)")
    if not dry:
        subprocess.run(cmd, check=True)


SITE_TOKENS = {"modrinth": "MODRINTH_TOKEN", "curseforge": "CURSEFORGE_TOKEN"}


def publish_modrinth(plan: dict, dry: bool, log, out: Path) -> None:
    project = plan["modrinth"]["project"]
    token = os.environ.get("MODRINTH_TOKEN")
    if not dry and not token:
        raise PublishError("MODRINTH_TOKEN is not set")
    existing = [] if dry else http_json("GET", f"{MODRINTH_API}/project/{project}/version", token)
    numbers = {v["version_number"] for v in existing}
    for f in plan["files"]:
        data = modrinth_payload(plan, f)
        if f["version_number"] in numbers:
            log(f"modrinth: {f['version_number']} already published, skipping")
            continue
        log(f"modrinth: create {data['name']} ({', '.join(f['game_versions'])})")
        if dry:
            continue
        http_multipart(f"{MODRINTH_API}/version", {"data": json.dumps(data)}, {"file": Path(f["universal"])},
                       {"Authorization": token})
        # Only the newest version per Minecraft range stays featured.
        for old in existing:
            if old.get("featured") and set(old["game_versions"]) <= set(f["game_versions"]):
                log(f"modrinth: unfeature {old['version_number']}")
                http_json("PATCH", f"{MODRINTH_API}/version/{old['id']}", token, {"featured": False})


def modrinth_payload(plan: dict, f: dict) -> dict:
    return {
        "project_id": plan["modrinth"]["project"],
        "name": f["display_name"],
        "version_number": f["version_number"],
        "changelog": plan["changelog"],
        "dependencies": plan["modrinth"]["dependencies"],
        "game_versions": f["game_versions"],
        "version_type": plan["mod"]["release_type"],
        "loaders": plan["modrinth"]["loaders"],
        "featured": True,
        "file_parts": ["file"],
        "primary_file": "file",
    }


def curseforge_payload(plan: dict, f: dict, version_ids: list[int]) -> dict:
    payload = {
        "changelog": plan["changelog"],
        "changelogType": "markdown",
        "displayName": f["display_name"],
        "gameVersions": version_ids,
        "releaseType": plan["mod"]["release_type"],
    }
    if plan["curseforge"]["relations"]:
        payload["relations"] = {"projects": plan["curseforge"]["relations"]}
    return payload


def publish_curseforge(plan: dict, dry: bool, log, out: Path) -> None:
    project = plan["curseforge"]["project"]
    token = os.environ.get("CURSEFORGE_TOKEN")
    if not token:
        if dry:
            log("curseforge: CURSEFORGE_TOKEN not set, game version ids not resolved (dry run)")
            for f in plan["files"]:
                log(f"curseforge: upload {f['display_name']} -> project {project}")
            return
        raise PublishError("CURSEFORGE_TOKEN is not set")
    cf = CurseForgeVersions.fetch(token)
    errors: list[str] = []
    resolved = [(f, cf.ids(f, plan, errors)) for f in plan["files"]]
    if errors:
        raise PublishError("; ".join(errors))
    for f, ids in resolved:
        payload = curseforge_payload(plan, f, ids)
        log(f"curseforge: upload {payload['displayName']} ({len(ids)} version tags)")
        if not dry:
            http_multipart(f"{CURSEFORGE_API}/projects/{project}/upload-file", {"metadata": json.dumps(payload)},
                           {"file": Path(f["universal"])}, {"X-Api-Token": token})


def github_assets(plan: dict) -> list[str]:
    assets = []
    for f in plan["files"]:
        assets += [p for p in [f["universal"], *f["platforms"].values(), f["sources"]] if p]
    return assets


def site_links(plan: dict) -> list[str]:
    links = []
    if plan["modrinth"]["project"]:
        links.append(f"[Modrinth](https://modrinth.com/mod/{plan['modrinth']['project']})")
    if plan["curseforge"]["project"]:
        slug = plan["curseforge"].get("slug")
        url = (f"https://www.curseforge.com/minecraft/mc-mods/{slug}" if slug
               else f"https://www.curseforge.com/projects/{plan['curseforge']['project']}")
        links.append(f"[CurseForge]({url})")
    return links


def github_release_body(plan: dict) -> str:
    mod = plan["mod"]
    lines = [plan["changelog"].strip(), "", "---", "", "## Downloads", ""]
    links = site_links(plan)
    if links:
        lines.append(f"Most players should install {mod['name']} from {' or '.join(links)} (launchers update it "
                     "for you). The files below are the same release, for manual installs and developers.")
        lines.append("")
    for f in plan["files"]:
        lines += [f"### Minecraft {range_text(f['game_versions'])}", "", "| File | For |", "|---|---|"]
        if f["universal"]:
            lines.append(f"| `{Path(f['universal']).name}` | Every system (recommended) |")
        for os_, path in f["platforms"].items():
            lines.append(f"| `{Path(path).name}` | {OS_LABELS[os_]} only |")
        if f["sources"]:
            lines.append(f"| `{Path(f['sources']).name}` | Source code, for developers |")
        lines.append("")
    if any(f["platforms"] for f in plan["files"]):
        lines.append("Per-system files are the universal jar with only that system's native libraries; "
                     "they behave identically on it. The pure-Java file has no native libraries and runs the "
                     "Java fallbacks everywhere.")
        lines.append("")
    lines.append("Verify downloads with `SHA256SUMS.txt`.")
    return "\n".join(lines).strip() + "\n"


def sync_modrinth(cfg: ModConfig, body: str, dry: bool, log) -> None:
    if not cfg.modrinth_id:
        log("modrinth: no project id, skipping description")
        return
    patch = {"body": body, "client_side": cfg.client_side, "server_side": cfg.server_side}
    links = {"source_url": cfg.source_url, "issues_url": cfg.issues_url}
    patch.update({k: v for k, v in links.items() if v})
    log(f"modrinth: PATCH project {cfg.modrinth_id} (body {len(body)} chars, client {cfg.client_side}, "
        f"server {cfg.server_side}" + "".join(f", {k} {v}" for k, v in links.items() if v) + ")")
    if not dry:
        token = os.environ.get("MODRINTH_TOKEN")
        if not token:
            raise PublishError("MODRINTH_TOKEN is not set")
        http_json("PATCH", f"{MODRINTH_API}/project/{cfg.modrinth_id}", token, patch)


# --------------------------------------------------------------------------- preview

PREVIEW_CSS = """
:root { --bg:#f6f7f9; --fg:#1b1f24; --muted:#5b6470; --card:#fff; --line:#d9dee5; --mr:#1bd96a; --cf:#f16436; --gh:#24292f; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#0f1115; --fg:#e6e9ee; --muted:#9aa4b2; --card:#171a21; --line:#2a2f3a; } }
:root[data-theme="dark"] { --bg:#0f1115; --fg:#e6e9ee; --muted:#9aa4b2; --card:#171a21; --line:#2a2f3a; }
* { box-sizing: border-box; } body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 system-ui, sans-serif; }
header { padding:16px; border-bottom:1px solid var(--line); } header h1 { margin:0; font-size:18px; }
.tabs { display:flex; gap:8px; flex-wrap:wrap; padding:12px 16px; }
.tabs button { border:1px solid var(--line); background:var(--card); color:var(--fg); padding:6px 12px; border-radius:6px; cursor:pointer; }
.tabs button[aria-selected=true] { border-color:var(--fg); font-weight:600; }
section { display:none; padding:0 16px 32px; } section.on { display:block; }
.page { max-width: 860px; margin:0 auto; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:20px; overflow-x:auto; }
.page img { max-width:100%; height:auto; } .page table { border-collapse:collapse; } .page th, .page td { border:1px solid var(--line); padding:4px 8px; }
.page pre { overflow-x:auto; background:var(--bg); padding:10px; border-radius:6px; } .page code { font-size:13px; }
.badge { display:inline-block; font-size:12px; padding:2px 8px; border-radius:99px; color:#fff; margin-bottom:12px; }
.mr { background:var(--mr); color:#0b2a17; } .cf { background:var(--cf); } .gh { background:var(--gh); }
.cf-page { font-family: Arial, Helvetica, sans-serif; } .cf-page h1, .cf-page h2 { border-bottom:1px solid var(--line); padding-bottom:4px; }
.warn { border-left:4px solid var(--cf); padding:8px 12px; background:var(--card); margin:0 auto 12px; max-width:860px; }
table.plan { width:100%; border-collapse:collapse; } table.plan td, table.plan th { border:1px solid var(--line); padding:6px; vertical-align:top; text-align:left; }
"""

PREVIEW_JS = """
document.querySelectorAll('.tabs button').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.tabs button').forEach(x => x.setAttribute('aria-selected', x === b));
  document.querySelectorAll('section').forEach(s => s.classList.toggle('on', s.id === b.dataset.tab));
}));
"""


def preview_html(plan: dict, modrinth_md: str, cf_html: str) -> str:
    mod = plan["mod"]
    rows = []
    for f in plan["files"]:
        cf_tags = f["game_versions"] + [plan["curseforge"]["loader"], f"Java {f['java']}"] + plan["curseforge"]["environments"]
        rows.append(
            "<tr>"
            f"<td><b>{html.escape(f['display_name'])}</b><br><code>{html.escape(f['version_number'])}</code></td>"
            f"<td>{html.escape(Path(f['universal']).name) if f['universal'] else '<i>not built</i>'}</td>"
            f"<td>{html.escape(', '.join(f['game_versions']))}<br>loader: neoforge<br>"
            f"deps: {html.escape(json.dumps(plan['modrinth']['dependencies']))}</td>"
            f"<td>{html.escape(', '.join(cf_tags))}<br>relations: {html.escape(json.dumps(plan['curseforge']['relations']))}</td>"
            f"<td>{'<br>'.join(html.escape(Path(p).name) for p in github_assets({'files': [f]})) or '<i>none</i>'}</td>"
            "</tr>")
    warnings = "".join(f'<div class="warn">README: {html.escape(w)}</div>' for w in plan["readme_warnings"])
    targets = []
    targets.append(f"Modrinth project <code>{plan['modrinth']['project']}</code>" if plan["modrinth"]["project"] else "Modrinth: <i>no project id, skipped</i>")
    targets.append(f"CurseForge project <code>{plan['curseforge']['project']}</code>" if plan["curseforge"]["project"] else "CurseForge: <i>no project id, skipped</i>")
    targets.append(f"GitHub release <code>{html.escape(str(plan['tag']))}</code> in {html.escape(mod['github'])}")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(mod['name'])} release preview</title>
<style>{PREVIEW_CSS}</style></head><body>
<header><h1>{html.escape(mod['name'])} {html.escape(mod['version'])} — release preview (not published)</h1></header>
<div class="tabs" role="tablist">
<button data-tab="mr" aria-selected="true">Modrinth page</button><button data-tab="cf" aria-selected="false">CurseForge page</button>
<button data-tab="gh" aria-selected="false">GitHub release</button><button data-tab="plan" aria-selected="false">Upload plan</button></div>
<section id="mr" class="on">{warnings}<div class="page"><span class="badge mr">Modrinth · description</span>{markdown_to_html(modrinth_md)}</div></section>
<section id="cf">{warnings}<div class="page cf-page"><span class="badge cf">CurseForge · description</span>{cf_html}</div></section>
<section id="gh"><div class="page"><span class="badge gh">GitHub · release {html.escape(str(plan['tag']))}</span>{markdown_to_html(github_release_body(plan))}</div></section>
<section id="plan"><div class="page"><p>{' · '.join(targets)}</p>
<p>Release type: <b>{mod['release_type']}</b> · Modrinth sides: client <b>{mod['client_side']}</b>, server <b>{mod['server_side']}</b></p>
<table class="plan"><tr><th>Version</th><th>Modrinth + CurseForge file</th><th>Modrinth</th><th>CurseForge tags</th><th>GitHub assets</th></tr>{''.join(rows)}</table>
<h3>Changelog (all sites)</h3>{markdown_to_html(plan['changelog'] or '*missing*')}</div></section>
<script>{PREVIEW_JS}</script></body></html>
"""


# --------------------------------------------------------------------------- cli


def load_env(mod: Path) -> None:
    """.env from the current directory, the mod and panzer-build-logic (tools/panzer_mod.py)."""
    tools = Path(__file__).resolve().parent.parent / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    try:
        from panzer_mod import BUILD_LOGIC, load_dotenv
    except ImportError:
        return
    load_dotenv(Path.cwd(), mod, BUILD_LOGIC)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["describe", "plan", "check", "preview", "publish", "sync"])
    p.add_argument("--mod", type=Path, default=Path("."), help="mod repository root")
    p.add_argument("--dist", type=Path, default=None, help="directory with the built jars (default <mod>/build/libs)")
    p.add_argument("--tag", default=None, help="release tag (default v<[mod] version>); must match the version")
    p.add_argument("--out", type=Path, default=Path("publish-out"))
    p.add_argument("--targets", default="github,modrinth,curseforge")
    p.add_argument("--dry-run", action="store_true", help="do everything except the uploads")
    p.add_argument("--offline", action="store_true", help="check without querying Modrinth/CurseForge")
    args = p.parse_args(argv)
    load_env(args.mod)

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    log_lines: list[str] = []

    def log(msg: str) -> None:
        print(msg)
        log_lines.append(msg)

    try:
        cfg = load_config(args.mod.resolve())
        readme = (cfg.root / "README.md").read_text(encoding="utf-8")
        modrinth_md = published_markdown(readme, cfg, "modrinth")
        (out / "modrinth.md").write_text(modrinth_md, encoding="utf-8")
        cf_html = curseforge_html(published_markdown(readme, cfg, "curseforge"))
        (out / "curseforge.html").write_text(cf_html, encoding="utf-8")
        for w in lint_readme(readme):
            print(f"::warning::README: {w}")
        if args.command == "describe":
            return 0
        if args.command == "sync":
            sync_modrinth(cfg, modrinth_md, args.dry_run, log)
            return 0

        dist = args.dist or (cfg.root / "build" / "libs")
        plan = build_plan(cfg, dist, args.tag or f"v{cfg.version}")
        (out / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
        (out / "github-release.md").write_text(github_release_body(plan), encoding="utf-8")
        (out / "preview.html").write_text(preview_html(plan, modrinth_md, cf_html), encoding="utf-8")
        if args.command in ("plan", "preview"):
            print(f"wrote {out / 'plan.json'} and {out / 'preview.html'}")
            return 0

        errors = check_plan(plan, online=not args.offline)
        for e in errors:
            print(f"::error::{e}")
        if args.command == "check" or errors:
            return 1 if errors else 0

        targets = [t.strip() for t in args.targets.split(",") if t.strip()]
        failures = []
        for target, fn, needed in (("github", publish_github, True),
                                   ("modrinth", publish_modrinth, plan["modrinth"]["project"]),
                                   ("curseforge", publish_curseforge, plan["curseforge"]["project"])):
            if target not in targets:
                continue
            if not needed:
                log(f"{target}: no project id configured, skipped")
                continue
            token = SITE_TOKENS.get(target)
            if token and not args.dry_run and not os.environ.get(token):
                # A private repository on GitHub Free does not get organization
                # secrets: publish the rest, say how to finish this site later.
                skipped = (f"{target}: {token} is not available to this repository, skipped; once it is set, "
                           f"run the CI on tag {plan['tag']} with dry_run off and targets={target}")
                log(skipped)
                print(f"::warning::{skipped}")
                continue
            try:
                fn(plan, args.dry_run, log, out)
            except (PublishError, subprocess.CalledProcessError) as e:
                failures.append(f"{target}: {e}")
                print(f"::error::{target}: {e}")
        (out / "publish.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
        return 1 if failures else 0
    except PublishError as e:
        print(f"::error::{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
