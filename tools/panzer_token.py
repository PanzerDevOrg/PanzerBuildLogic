"""`panzer token-check`: is PANZER_SYNC_TOKEN good enough for the Mods workflow?

Checks, per repository, without changing anything:
  valid      the token authenticates at all (API /user)
  visible    the repository API answers (a fine-grained token must have it selected;
             a public repository answers any valid token, so this alone proves little)
  read       git can fetch (Contents: read)
  write      git would accept a push: GitHub answers the receive-pack handshake only
             for tokens allowed to push (Contents: read and write)
  workflows  classic tokens list their scopes, so `workflow` is checked; fine-grained
             tokens cannot be asked, so this is proven by the first sync that changes
             a file under .github/workflows (GitHub rejects that push otherwise)
"""
from __future__ import annotations

import base64
import dataclasses
import json
import urllib.error
import urllib.request

API = "https://api.github.com"
USER_AGENT = "PanzerDevOrg/panzer-token-check"


@dataclasses.dataclass
class Response:
    status: int
    headers: dict[str, str]
    message: str = ""
    data: dict = dataclasses.field(default_factory=dict)


def _request(url: str, headers: dict[str, str]) -> Response:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(request, timeout=30) as r:
            return _response(r.status, r.headers, r.read(65536))
    except urllib.error.HTTPError as e:
        return _response(e.code, e.headers, e.read(65536))
    except urllib.error.URLError as e:
        return Response(0, {}, str(e.reason))


def _response(status: int, headers, body: bytes) -> Response:
    lowered = {k.lower(): v for k, v in headers.items()}
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return Response(status, lowered, body.decode("utf-8", "replace").strip()[:200])
    if not isinstance(data, dict):
        return Response(status, lowered)
    return Response(status, lowered, str(data.get("message", "")), data)


def api(token: str, path: str) -> Response:
    return _request(f"{API}{path}", {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                    "X-GitHub-Api-Version": "2022-11-28"})


def git_service(token: str, repo: str, service: str) -> Response:
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return _request(f"https://github.com/{repo}.git/info/refs?service={service}",
                    {"Authorization": f"Basic {basic}"})


def kind(token: str) -> str:
    if token.startswith("github_pat_"):
        return "fine-grained"
    if token.startswith(("ghp_", "gho_")):
        return "classic"
    return "unknown"


@dataclasses.dataclass
class RepoResult:
    repo: str
    visible: bool
    read: bool
    write: bool
    problems: list[str]


def classify(repo: str, token_kind: str, repo_api: Response, upload: Response, receive: Response) -> RepoResult:
    """Turns the three answers for one repository into a verdict and fixes."""
    problems = []
    visible = repo_api.status == 200
    read = upload.status == 200
    write = receive.status == 200
    if repo_api.status == 404:
        problems.append("the token cannot see this repository: select it under Repository access "
                        "(fine-grained token owned by the organization)")
    elif repo_api.status == 403:
        problems.append(f"access refused ({repo_api.message or 'forbidden'}): the organization may still "
                        "have to approve the token, or forbids this kind of token")
    elif repo_api.status not in (200, 404, 403):
        problems.append(f"repository API answered {repo_api.status} {repo_api.message}".strip())
    if not read:
        problems.append(f"git fetch refused ({upload.status}): needs Contents: Read")
    if not write:
        if receive.status == 403:
            problems.append("git push refused (403): needs Contents: Read and write")
        elif receive.status in (401, 404):
            problems.append(f"git push refused ({receive.status}): the token has no access to this repository")
        else:
            problems.append(f"git push handshake answered {receive.status} {receive.message}".strip())
    return RepoResult(repo, visible, read, write, problems)


def check(token: str, repos: list[str]) -> tuple[list[str], bool]:
    """Report lines and overall success."""
    lines, ok = [], True
    token_kind = kind(token)
    user = api(token, "/user")
    if user.status == 401:
        return ["The token is invalid or expired (GitHub answered 401 Bad credentials). Create a new one."], False
    if user.status != 200:
        lines.append(f"GET /user answered {user.status} {user.message}".strip())
    lines.append(f"Token type: {token_kind}")
    lines.append(f"Acts as: {user.data.get('login', 'unknown')}")
    expiry = user.headers.get("github-authentication-token-expiration")
    lines.append(f"Expires: {expiry or 'never (or not reported)'}")
    scopes = user.headers.get("x-oauth-scopes")
    if token_kind == "classic":
        granted = {s.strip() for s in (scopes or "").split(",") if s.strip()}
        missing = {"repo", "workflow"} - granted
        lines.append(f"Classic scopes: {', '.join(sorted(granted)) or 'none'}")
        if missing:
            ok = False
            lines.append(f"  missing scopes: {', '.join(sorted(missing))}")
    lines.append("")
    lines.append("| Repository | Visible | Read | Push | Problems |")
    lines.append("|---|---|---|---|---|")
    for repo in repos:
        result = classify(repo, token_kind, api(token, f"/repos/{repo}"),
                          git_service(token, repo, "git-upload-pack"),
                          git_service(token, repo, "git-receive-pack"))
        ok &= result.read and result.write
        mark = lambda b: "yes" if b else "**no**"
        lines.append(f"| {repo} | {mark(result.visible)} | {mark(result.read)} | {mark(result.write)} | "
                     f"{'; '.join(result.problems) or '-'} |")
    lines.append("")
    if token_kind == "fine-grained":
        lines.append("Workflows permission: fine-grained tokens cannot be asked for it. It is proven by the "
                     "first sync that changes a file under .github/workflows (GitHub refuses that push "
                     "without Workflows: Read and write).")
    elif token_kind == "unknown":
        lines.append("Token type not recognised (expected github_pat_... or ghp_...).")
    return lines, ok
