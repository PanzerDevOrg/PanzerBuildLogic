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

The token itself is never printed: only its type, length and what looks wrong
with how it was pasted. Network failures are reported as such, never as a
missing permission.
"""
from __future__ import annotations

import base64
import dataclasses
import http.client
import json
import re
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
USER_AGENT = "PanzerDevOrg/panzer-token-check"
TOKEN_CHARS = re.compile(r"[A-Za-z0-9_]+")
# Lengths of well-formed tokens: github_pat_ + 82 characters, ghp_ + 36.
EXPECTED_LENGTH = {"fine-grained": 93, "classic": 40}
RETRY_DELAY = 2.0  # seconds before the one retry of a request GitHub or the network failed


@dataclasses.dataclass
class Response:
    status: int  # 0: no answer (network, timeout, unsendable header)
    headers: dict[str, str]
    message: str = ""
    data: dict = dataclasses.field(default_factory=dict)

    @property
    def unreachable(self) -> bool:
        """GitHub or the network failed: says nothing about the token."""
        return self.status == 0 or self.status == 429 or self.status >= 500 or (
            self.status == 403 and "rate limit" in self.message.lower())


def _request(url: str, headers: dict[str, str], attempts: int = 2) -> Response:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    result = Response(0, {})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=30) as r:
                result = _response(r.status, r.headers, r.read(65536))
        except urllib.error.HTTPError as e:
            result = _response(e.code, e.headers, e.read(65536))
        except ValueError:
            # An unsendable header (from the token) or URL; the exception text
            # would contain the token, so it is not kept. UnicodeError is a ValueError.
            return Response(0, {}, "the request could not be built (a header or the URL has invalid characters)")
        except urllib.error.URLError as e:
            result = Response(0, {}, f"network error: {type(e.reason).__name__}")
        except (OSError, http.client.HTTPException) as e:
            result = Response(0, {}, f"network error: {type(e).__name__}")
        if not result.unreachable or attempt == attempts - 1:
            return result
        time.sleep(RETRY_DELAY)
    return result


def _response(status: int, headers, body: bytes) -> Response:
    lowered = {k.lower(): v for k, v in headers.items()}
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return Response(status, lowered, body.decode("utf-8", "replace").strip()[:120])
    if isinstance(data, list):
        return Response(status, lowered, "", {"rules": data})
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


def cell(text: str) -> str:
    """Safe inside a markdown table cell."""
    return " ".join(text.split()).replace("|", "/")


@dataclasses.dataclass
class RepoResult:
    repo: str
    visible: bool
    read: bool
    write: bool
    problems: list[str]
    unchecked: bool = False  # GitHub or the network failed for this repository
    private: bool | None = None


def classify(repo: str, token_kind: str, repo_api: Response, upload: Response, receive: Response) -> RepoResult:
    """Turns the three answers for one repository into a verdict and fixes.

    Public repositories answer reads to any valid token, so for them only the
    push handshake tells anything, and a refused push has several causes.
    """
    failed = [r for r in (repo_api, upload, receive) if r.unreachable]
    if failed:
        r = failed[0]
        reason = f"{r.status} {cell(r.message)}" if r.status else cell(r.message)
        return RepoResult(repo, False, False, False,
                          [f"could not check ({reason}): GitHub or the network failed, run it again"], unchecked=True)
    problems = []
    visible = repo_api.status == 200
    private = repo_api.data.get("private") if visible else None
    read = upload.status == 200
    write = receive.status == 200
    classic = token_kind == "classic"
    if repo_api.status == 404:
        problems.append("not visible to the token: it needs the repo scope, and your account access to it"
                        if classic else
                        "not visible to the token: Resource owner must be PanzerDevOrg, this repository selected "
                        "under Repository access, and the token approved by PanzerDevOrg")
    elif repo_api.status == 403:
        problems.append(f"access refused ({cell(repo_api.message) or 'forbidden'}): PanzerDevOrg may still have to "
                        "approve the token, or forbids this kind of token")
    elif not visible:
        problems.append(f"repository API answered {repo_api.status} {cell(repo_api.message)}".strip())
    if not read and visible:
        problems.append(f"git fetch refused ({upload.status} {cell(upload.message)})".strip())
    if not write and visible:
        said = f" (GitHub: {cell(receive.message)})" if receive.message and receive.status in (401, 403, 404) else ""
        if receive.status == 403 and classic:
            problems.append(f"git push refused{said}: your account has no write role here, or PanzerDevOrg blocks "
                            "classic tokens (organization Settings > Personal access tokens)")
        elif receive.status == 403 and private:
            problems.append(f"git push refused{said}: set Contents to Read and write")
        elif receive.status in (401, 403, 404):
            problems.append(f"git push refused{said}: check, in this order, Resource owner = PanzerDevOrg (not your "
                            "user), this repository selected under Repository access, the token not pending "
                            "approval in PanzerDevOrg, and Contents: Read and write")
        else:
            problems.append(f"git push handshake answered {receive.status} {cell(receive.message)}".strip())
    return RepoResult(repo, visible, read, write, problems, private=private)


def master_rules(token: str, repo: str) -> list[str]:
    """Branch protection or rulesets on master that could refuse the sync's
    direct push (they are enforced only on the real push)."""
    notes = []
    branch = api(token, f"/repos/{repo}/branches/master")
    if branch.status == 404:
        return ["warning: no master branch (the sync pushes to master)"]
    if branch.status == 200 and branch.data.get("protected"):
        notes.append("warning: master is protected; a direct push may be refused")
    rules = api(token, f"/repos/{repo}/rules/branches/master")
    if rules.status == 200:
        kinds = sorted({r.get("type", "?") for r in (rules.data.get("rules") or [])}) if isinstance(rules.data, dict) else []
        blocking = [k for k in kinds if k in ("pull_request", "required_status_checks", "update", "non_fast_forward",
                                              "required_signatures", "required_linear_history")]
        if blocking:
            notes.append(f"warning: rulesets on master ({', '.join(blocking)}) may refuse a direct push")
    return notes


def shape(raw: str) -> list[str]:
    """What is wrong with how the secret was pasted, without revealing it."""
    notes = []
    token = raw.strip()
    if raw != token:
        notes.append("the secret has spaces or line breaks around the token (they are trimmed where it is "
                     "used, but save only the token itself)")
    if not TOKEN_CHARS.fullmatch(token):
        notes.append("the secret contains characters a token never has (spaces, line breaks, quotes, "
                     "accented or invisible characters); save only the token itself")
    token_kind = kind(token)
    if token_kind == "unknown":
        notes.append("it does not start with github_pat_ (fine-grained) or ghp_ (classic), so it is probably "
                     "not a token: e.g. the token's name, its page URL, or an SSH key")
    elif len(token) != EXPECTED_LENGTH[token_kind]:
        notes.append(f"a {token_kind} token is usually {EXPECTED_LENGTH[token_kind]} characters long and this one "
                     f"has {len(token)}: it may have been copied incompletely")
    return notes


def _badly_pasted(token_kind: str, token: str, notes: list[str], headline: str, rejected: bool) -> list[str]:
    lines = [f"{headline} Type: {token_kind}, {len(token)} characters.", ""]
    if notes:
        lines += ["What looks wrong:", *[f"- {n}" for n in notes], ""]
    if rejected:
        lines.append(("Otherwise it" if notes else "It") + " was revoked, regenerated, deleted or has expired.")
    lines.append("Create a new one (see .env.example), copy it right after GitHub shows it (it is shown only "
                 "once), and save only that value as the secret.")
    return lines


def check(raw: str, repos: list[str]) -> tuple[list[str], bool, list[str]]:
    """Report lines, overall success and warnings worth an annotation.
    `raw` is the secret exactly as stored."""
    token = raw.strip()
    token_kind = kind(token)
    notes = shape(raw)
    if not TOKEN_CHARS.fullmatch(token):
        # Not sendable as-is (and certainly not a token): no request is made.
        return _badly_pasted(token_kind, token, notes, "This is not a usable token.", rejected=False), False, []
    user = api(token, "/user")
    if user.status == 401:
        return _badly_pasted(token_kind, token, notes, "GitHub does not accept this token (401 Bad credentials).",
                             rejected=True), False, []
    if user.status != 200:
        return [f"Could not check the token: GitHub answered {user.status} {cell(user.message)}".strip()
                + ". GitHub or the network failed; run it again."], False, []

    # GitHub accepted it, so length/prefix guesses are only informative now;
    # surrounding whitespace is trimmed everywhere the token is used.
    lines, ok, warnings = [], True, []
    lines += [f"Note: {n}" for n in notes if "spaces or line breaks around" not in n]
    lines.append(f"Token type: {token_kind}")
    lines.append(f"Acts as: {user.data.get('login', 'unknown')}")
    expiry = user.headers.get("github-authentication-token-expiration")
    lines.append(f"Expires: {expiry or 'never (or not reported)'}")
    if token_kind == "classic":
        granted = {s.strip() for s in user.headers.get("x-oauth-scopes", "").split(",") if s.strip()}
        missing = {"repo", "workflow"} - granted
        lines.append(f"Classic scopes: {', '.join(sorted(granted)) or 'none'}")
        if missing:
            ok = False
            lines.append(f"  missing scopes: {', '.join(sorted(missing))}")
    lines += ["", "| Repository | Visible | Read | Push | Problems |", "|---|---|---|---|---|"]
    mark = lambda b, unchecked: "?" if unchecked else ("yes" if b else "**no**")
    results = []
    for repo in repos:
        result = classify(repo, token_kind, api(token, f"/repos/{repo}"),
                          git_service(token, repo, "git-upload-pack"),
                          git_service(token, repo, "git-receive-pack"))
        if result.write:
            for note in master_rules(token, repo):
                result.problems.append(note)
                warnings.append(f"{repo}: {note}")
        results.append(result)
        ok &= result.read and result.write
        u = result.unchecked
        lines.append(f"| {repo} | {mark(result.visible, u)} | {mark(result.read, u)} | {mark(result.write, u)} | "
                     f"{'; '.join(result.problems) or '-'} |")
    lines.append("")
    checked = [r for r in results if not r.unchecked]
    if token_kind == "fine-grained" and checked and not any(r.write for r in checked) \
            and any(not r.visible for r in checked):
        lines += ["Most likely cause: the token's Resource owner is your user instead of PanzerDevOrg, or "
                  "PanzerDevOrg has not approved it yet (organization Settings > Personal access tokens > "
                  "Pending requests).", ""]
    if not ok:
        lines.append("Result: the token is NOT ready; fix the problems above (see .env.example).")
    elif token_kind == "fine-grained":
        unverified = ("Workflows permission not verifiable: open the token at "
                      "https://github.com/settings/personal-access-tokens and confirm its repository permissions "
                      "list 'Workflows: Read and write'. Without it GitHub refuses every sync that changes "
                      ".github/workflows/.")
        warnings.append(unverified)
        lines += [unverified, "",
                  "Result: read and push work on every mod; only the Workflows permission above is unconfirmed."]
    else:
        lines.append("Result: the token can do everything the Mods workflow needs.")
    return lines, ok, warnings
