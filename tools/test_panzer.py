"""Offline tests for the panzer tool (python3 -m unittest discover -s tools)."""
import json
import os
import shutil
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

import panzer
import panzer_ci
import panzer_sync
from panzer_legal import Legal
from panzer_mod import BUILD_LOGIC, PanzerError, load_mod, merge_tables, parse_dotenv

MOD_TOML = textwrap.dedent('''
    [mod]
    id = "demo"
    name = "Demo"
    group = "com.panzer.mods"
    package = "com.panzer.mods.demo"
    version = "1.0.0"
    issues = "https://github.com/PanzerDevOrg/Demo/issues"

    [natives.demo_native]
    cmake_dir = "native"
    platforms = ["windows-x86_64", "linux-x86_64", "linux-aarch64", "macos-aarch64"]
    ci_prepare = "native/fetch.sh"
    ci_linux_script = "native/build-linux.sh"

    [natives.zstd]
    platforms = ["linux-x86_64", "windows-x86_64"]
    jar_name_linux = "libzstd.so.1"

    [legal.third_party]
    components = ["zstd"]

    [depends_on.celeris]
    version = "0.2.0"
    artifact = "com.panzer.mods:celeris-{mc}"
    repo = "PanzerDevOrg/Celeris"
    ci_build_from_source = true

    [publish]
    platforms = ["windows", "linux", "java"]

    [stonecutter]
    versions = ["1.21.1", "26.1"]
    vcs_version = "1.21.1"

    ["26.1"]
    minecraft_version = "26.1"
''')

README = textwrap.dedent('''
    # Demo

    <!-- publish:off -->
    <!-- panzer:license -->
    old license text
    <!-- /panzer:license -->
    <!-- publish:on -->

    <!-- panzer:footer -->
    <!-- /panzer:footer -->
''')


def make_mod(tmp: Path) -> Path:
    root = tmp / "Demo"
    root.mkdir()
    (root / "mod.stonecutter.properties.toml").write_text(MOD_TOML)
    (root / "README.md").write_text(README)
    (root / ".gitignore").write_text("build/\n# repomix output\nrepomix*\n\n*.log\n")
    (root / "stonecutter.gradle.kts").write_text('plugins {}\n\nstonecutter active "26.1"\n')
    (root / "src/main/resources").mkdir(parents=True)
    (root / "src/main/resources/LICENSE").write_text("old copy")
    return root


class DotEnv(unittest.TestCase):
    def test_parse(self):
        values = parse_dotenv('# c\nexport A=1\nB="two words"\nC=3 # note\nbad line\nD=\n')
        self.assertEqual(values, {"A": "1", "B": "two words", "C": "3", "D": ""})


class Merge(unittest.TestCase):
    def test_tables_replace_whole_but_subtables_merge(self):
        common = {"legal": {"holder": "P", "code": "X"}, "plugins": {"a": "1"}}
        mod = {"legal": {"third_party": {"components": ["zstd"]}}, "plugins": {"b": "2"}}
        merged = merge_tables(common, mod)
        self.assertEqual(merged["legal"], {"holder": "P", "code": "X", "third_party": {"components": ["zstd"]}})
        self.assertEqual(merged["plugins"], {"b": "2"})


class Sync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_mod(Path(self.tmp.name))
        self.mod = load_mod(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_legal_files(self):
        files = Legal(self.mod).files()
        self.assertIn("DEMO LICENSING", files["LICENSE"])
        self.assertIn("Copyright (c) Panzer", files["LICENSE"])
        self.assertIn("Zstandard (zstd) (BSD-3-Clause)", files["LICENSE"])
        self.assertTrue(files["LICENSE-AGPL"].startswith("GNU AFFERO GENERAL PUBLIC LICENSE"))
        self.assertIn("Attribution-NonCommercial-ShareAlike", files["LICENSE-CC"])
        self.assertIn("Redistributions in binary form", files["NOTICE"])

    def test_unknown_component_is_an_error(self):
        toml = self.root / "mod.stonecutter.properties.toml"
        toml.write_text(toml.read_text().replace('components = ["zstd"]', 'components = ["nope"]'))
        with self.assertRaises(PanzerError):
            Legal(load_mod(self.root))

    def test_plan_and_apply(self):
        plan = panzer_sync.plan(self.mod)
        paths = {c.path: c.action for c in plan.changes}
        self.assertEqual(paths["src/main/resources/LICENSE"], "delete")
        self.assertEqual(paths["settings.gradle.kts"], "create")
        self.assertEqual(paths["gradlew"], "create")
        panzer_sync.apply(plan)
        self.assertEqual(panzer_sync.plan(load_mod(self.root)).changes, [])
        self.assertTrue(os.access(self.root / "gradlew", os.X_OK))
        self.assertIn('stonecutter active "26.1"', (self.root / "stonecutter.gradle.kts").read_text())
        self.assertIn("panzer.stonecutter", (self.root / "stonecutter.gradle.kts").read_text())
        readme = (self.root / "README.md").read_text()
        self.assertNotIn("old license text", readme)
        self.assertIn("| Zstandard (zstd) (bundled) |", readme)
        self.assertIn("Made by **Panzer**", readme)
        ignore = (self.root / ".gitignore").read_text()
        self.assertTrue(ignore.startswith(panzer_sync.GITIGNORE_BEGIN))
        self.assertEqual(ignore.count("repomix*"), 1)
        self.assertNotIn("# repomix output", ignore)
        self.assertIn("*.log", ignore)
        self.assertTrue((self.root / ".github/workflows/ci.yml").is_file())

    def test_missing_readme_block_is_a_note(self):
        (self.root / "README.md").write_text("# Demo\n")
        notes = panzer_sync.plan(load_mod(self.root)).notes
        self.assertTrue(any("panzer:license" in n for n in notes))

    def test_keep_lines(self):
        out = panzer_sync.keep_lines('a\nstonecutter active "1.21.1"\n', 'x\nstonecutter active "26.1"\n',
                                     ["^stonecutter active "])
        self.assertEqual(out, 'a\nstonecutter active "26.1"\n')


class Ci(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_mod(Path(self.tmp.name))
        self.mod = load_mod(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan(self):
        plan = panzer_ci.plan(self.mod)
        matrix = json.loads(plan["natives"])["include"]
        keys = sorted(e["key"] for e in matrix)
        self.assertEqual(keys, ["linux", "macos-aarch64", "windows-x86_64"])
        linux = next(e for e in matrix if e["key"] == "linux")
        self.assertEqual(linux["script"], "native/build-linux.sh")
        self.assertIn("natives/linux/aarch64/libdemo_native.so", linux["outputs"])
        win = next(e for e in matrix if e["key"] == "windows-x86_64")
        self.assertEqual((win["runner"], win["file"], win["prepare"]), ("windows-latest", "demo_native.dll", "native/fetch.sh"))
        self.assertEqual(plan["java"], "25\n21")
        self.assertEqual(json.loads(plan["source-deps"]), [{"id": "celeris", "repo": "PanzerDevOrg/Celeris", "versions": "1.21.1,26.1"}])
        self.assertEqual(plan["has-natives"], "true")

    def write_jars(self, natives_in_universal: list[str]):
        libs = self.root / "build" / "libs" / "1.0.0"
        libs.mkdir(parents=True)
        legal = ["META-INF/LICENSE", "META-INF/LICENSE-AGPL", "META-INF/LICENSE-CC", "META-INF/NOTICE"]
        base = libs / "demo-1.0.0+1.21.1"

        def jar(path, entries):
            with zipfile.ZipFile(path, "w") as z:
                for e in entries:
                    z.writestr(e, "x")
        jar(f"{base}.jar", legal + natives_in_universal)
        jar(f"{base}-sources.jar", ["META-INF/LICENSE"])
        for os_name in ("windows", "linux"):
            jar(f"{base}-{os_name}.jar", [n for n in natives_in_universal if n.startswith(f"natives/{os_name}-")])
        jar(f"{base}-java.jar", legal)
        return self.root / "build" / "libs"

    def test_verify_jars(self):
        everything = [panzer_ci.jar_entry(lib, spec, p) for lib, spec in self.mod.natives.items()
                      for p in spec["platforms"]]
        self.assertIn("natives/linux-x86_64/libzstd.so.1", everything)
        libs = self.write_jars(everything)
        errors, report = panzer_ci.verify_jars(self.mod, libs, strict=True)
        self.assertEqual(errors, [])
        self.assertEqual(len(report), 1)

    def test_verify_jars_reports_missing_native(self):
        libs = self.write_jars(["natives/linux-x86_64/libzstd.so.1"])
        errors, _ = panzer_ci.verify_jars(self.mod, libs, strict=True)
        self.assertTrue(any("windows-x86_64/zstd.dll" in e for e in errors))


class Selection(unittest.TestCase):
    def test_keys_are_case_and_space_insensitive(self):
        self.assertEqual([m.key for m in panzer.select_mods(" Velox , CELERIS")], ["celeris", "velox"])
        self.assertEqual(len(panzer.select_mods("all")), len(panzer.select_mods("")))

    def test_unknown_key_is_an_error_with_exit_code_2(self):
        with self.assertRaises(PanzerError):
            panzer.select_mods("nope")
        self.assertEqual(panzer.main(["mods", "--select", "nope"]), 2)

    def test_private_flag(self):
        self.assertTrue(next(m for m in panzer.select_mods("velox")).private)


class New(unittest.TestCase):
    def test_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "Shiny"
            self.assertEqual(panzer.main(["new", str(dest), "--id", "shiny"]), 0)
            self.assertTrue((dest / "src/main/java/com/panzer/mods/shiny/Shiny.java").is_file())
            self.assertIn('id = "shiny"', (dest / "mod.stonecutter.properties.toml").read_text())
            self.assertTrue((dest / "LICENSE-AGPL").is_file())
            self.assertIn("Made by **Panzer**", (dest / "README.md").read_text())
            self.assertEqual(panzer_sync.plan(load_mod(dest)).changes, [])


class Manifest(unittest.TestCase):
    def test_every_source_exists(self):
        for entry in panzer_sync.manifest()["file"]:
            source = BUILD_LOGIC / entry.get("from", f"shared/mod/{entry['path']}")
            self.assertTrue(source.is_file(), source)



FINE = "github_pat_" + "a" * 82
CLASSIC = "ghp_" + "a" * 36


class TokenCheck(unittest.TestCase):
    """panzer_token with GitHub's answers simulated."""

    def setUp(self):
        import panzer_token
        self.pt = panzer_token
        self.saved = (panzer_token.api, panzer_token.git_service)
        self.delay, panzer_token.RETRY_DELAY = panzer_token.RETRY_DELAY, 0

    def tearDown(self):
        self.pt.api, self.pt.git_service = self.saved
        self.pt.RETRY_DELAY = self.delay

    def fake(self, user_status=200, repo_status=200, upload=200, receive=200, headers=None, private=False,
             protected=False, rules=()):
        R = self.pt.Response

        def api(token, path):
            if path == "/user":
                return R(user_status, headers or {}, "", {"login": "bichal"})
            if "/rules/branches/" in path:
                return R(200, {}, "", {"rules": [{"type": t} for t in rules]})
            if path.endswith("/branches/master"):
                return R(200, {}, "", {"protected": protected})
            return R(repo_status, {}, "Not Found" if repo_status == 404 else "", {"private": private})
        self.pt.api = api
        self.pt.git_service = lambda token, repo, svc: R(upload if svc == "git-upload-pack" else receive, {},
                                                         "Write access to repository not granted." if receive == 403 else "")

    def run_check(self, token=FINE, repos=("PanzerDevOrg/A",)):
        r = self.pt.check(token, list(repos))
        return "\n".join(r.lines), r.ok, r.warnings

    def test_good_fine_grained_leaves_workflows_unconfirmed(self):
        self.fake(headers={"github-authentication-token-expiration": "2027-01-01 00:00:00 UTC"})
        text, ok, warnings = self.run_check()
        self.assertTrue(ok)
        self.assertIn("Acts as: bichal", text)
        self.assertIn("2027-01-01", text)
        self.assertIn("only the Workflows permission above is unconfirmed", text)
        self.assertTrue(any("Workflows" in w for w in warnings))

    def test_invalid(self):
        self.fake(user_status=401)
        text, ok, _ = self.run_check()
        self.assertFalse(ok)
        self.assertIn("401 Bad credentials", text)
        self.assertIn("fine-grained, 93 characters", text)
        self.assertNotIn(FINE, text)

    def test_badly_pasted(self):
        self.fake(user_status=401)
        text = self.run_check(FINE[:50])[0]
        self.assertIn("copied incompletely", text)
        text = self.run_check("my sync token")[0]
        self.assertIn("characters a token never has", text)
        self.assertIn("probably not a token", text)
        text = self.run_check("abcde")[0]
        self.assertIn("unknown, 5 characters", text)
        self.assertIn("probably not a token", text)

    def test_unicode_space_around_the_token_is_not_trimmed(self):
        self.fake()
        text, ok, _ = self.run_check("\u00a0" + FINE)
        self.assertFalse(ok)
        self.assertIn("characters a token never has", text)

    def test_whitespace_around_an_accepted_token_is_fine(self):
        self.fake()
        text, ok, _ = self.run_check(FINE + "\n")
        self.assertTrue(ok)

    def test_private_repo_read_only(self):
        self.fake(receive=403, private=True)
        text, ok, _ = self.run_check()
        self.assertFalse(ok)
        self.assertIn("set Contents to Read and write", text)
        self.assertIn("Write access to repository not granted", text)

    def test_public_repo_push_refused_lists_every_cause(self):
        self.fake(receive=403, private=False)
        text, ok, _ = self.run_check()
        self.assertFalse(ok)
        self.assertIn("Resource owner = PanzerDevOrg", text)
        self.assertIn("pending approval", text)

    def test_user_owned_token_diagnosis(self):
        R = self.pt.Response
        self.fake(receive=403)
        public_api = self.pt.api
        self.pt.api = lambda token, path: (R(404, {}, "Not Found") if path == "/repos/PanzerDevOrg/velox"
                                           else public_api(token, path))
        self.pt.git_service = lambda token, repo, svc: R(
            404 if repo.endswith("velox") else (200 if svc == "git-upload-pack" else 403), {})
        text, ok, _ = self.run_check(repos=("PanzerDevOrg/Celeris", "PanzerDevOrg/velox"))
        self.assertFalse(ok)
        self.assertIn("Most likely cause", text)
        self.assertIn("not visible to the token", text)

    def test_classic_scopes_and_wording(self):
        self.fake(headers={"x-oauth-scopes": "repo"})
        text, ok, _ = self.run_check(CLASSIC)
        self.assertFalse(ok)
        self.assertIn("missing scopes: workflow", text)
        self.fake(headers={"x-oauth-scopes": "repo, workflow"})
        text, ok, _ = self.run_check(CLASSIC)
        self.assertTrue(ok)
        self.assertIn("can do everything", text)
        self.fake(headers={"x-oauth-scopes": "repo, workflow"}, receive=403)
        self.assertIn("no write role", self.run_check(CLASSIC)[0])

    def test_master_protection_is_a_warning(self):
        self.fake(protected=True, rules=("pull_request",))
        text, ok, warnings = self.run_check()
        self.assertTrue(ok)
        self.assertIn("master is protected", text)
        self.assertTrue(any("pull_request" in w for w in warnings))

    def test_unsendable_token_makes_no_request_and_never_leaks(self):
        calls = []
        self.pt.api = lambda *a: calls.append(a)
        self.pt.git_service = lambda *a: calls.append(a)
        for raw in (FINE[:40] + "\n" + FINE[40:], "\u201c" + FINE + "\u201d", FINE[:40] + "\r\n" + FINE[40:],
                    FINE[:40] + "\n " + FINE[40:], FINE[:40] + "\u200b" + FINE[40:]):
            text, ok, _ = self.run_check(raw)
            self.assertFalse(ok)
            self.assertIn("characters a token never has", text)
            self.assertNotIn(FINE[:40], text)
            self.assertNotIn(FINE[40:], text)
        self.assertEqual(calls, [])

    def test_network_failures_are_not_permission_problems(self):
        R = self.pt.Response
        self.fake()
        self.pt.git_service = lambda token, repo, svc: R(0 if svc == "git-receive-pack" else 200, {},
                                                         "network error: TimeoutError")
        text, ok, _ = self.run_check()
        self.assertFalse(ok)
        self.assertIn("could not check (network error: TimeoutError)", text)
        self.assertNotIn("Resource owner", text)
        self.pt.api = lambda token, path: R(503, {}, "Service Unavailable")
        self.assertIn("GitHub or the network failed", self.run_check()[0])

    def test_request_survives_exceptions(self):
        import http.client
        import urllib.request
        saved = urllib.request.urlopen
        try:
            for error in (TimeoutError(), http.client.RemoteDisconnected("x"), ConnectionResetError()):
                def boom(*a, error=error, **k):
                    raise error
                urllib.request.urlopen = boom
                r = self.saved[0](FINE, "/user")
                self.assertEqual(r.status, 0)
                self.assertIn("network error", r.message)
        finally:
            urllib.request.urlopen = saved

    def test_unsendable_header_is_caught_without_leaking(self):
        # Real urlopen: the header error fires before connecting; if it ever
        # stopped firing, the request would go to 127.0.0.1, not GitHub.
        for bad in (FINE[:40] + "\n" + FINE[40:], "\u201c" + FINE):
            r = self.pt._request("http://127.0.0.1:9/", {"Authorization": f"Bearer {bad}"}, attempts=1)
            self.assertEqual(r.status, 0)
            self.assertIn("could not be built", r.message)
            self.assertNotIn(FINE[:40], r.message)

    def test_message_cannot_break_the_table(self):
        R = self.pt.Response
        result = self.pt.classify("PanzerDevOrg/A", "fine-grained", R(200, {}), R(200, {}), R(418, {}, "a|b\nc"))
        self.assertNotIn("|", result.problems[0])
        self.assertNotIn("\n", result.problems[0])

    def test_read_only_needs_no_push(self):
        self.fake(receive=403, private=True)
        r = self.pt.check(FINE, ["PanzerDevOrg/A"], need_write=False)
        self.assertTrue(r.ok)
        self.assertIn("can read every selected mod", "\n".join(r.lines))

    def test_error_body_read_failure_is_a_network_error(self):
        import io
        import urllib.error
        import urllib.request

        class Body(io.BytesIO):
            def read(self, *a):
                raise TimeoutError()
        saved = urllib.request.urlopen
        try:
            def fail(*a, **k):
                raise urllib.error.HTTPError("https://api.github.com/user", 502, "Bad Gateway", {}, Body())
            urllib.request.urlopen = fail
            r = self.saved[0](FINE, "/user")
            self.assertEqual(r.status, 0)
            self.assertTrue(r.unreachable)
        finally:
            urllib.request.urlopen = saved

    def test_gate_only_stops_an_unusable_token(self):
        import panzer
        R = self.pt.Response
        self.fake()
        good_api = self.pt.api
        self.pt.api = lambda token, path: R(404, {}, "Not Found") if path == "/repos/PanzerDevOrg/velox" else good_api(token, path)
        self.pt.git_service = lambda token, repo, svc: R(404 if repo.endswith("velox") else 200, {})
        os.environ["PANZER_SYNC_TOKEN"] = FINE
        try:
            self.assertEqual(panzer.main(["token-check"]), 1)
            self.assertEqual(panzer.main(["token-check", "--gate"]), 0)
            self.fake(user_status=401)
            self.assertEqual(panzer.main(["token-check", "--gate"]), 1)
        finally:
            del os.environ["PANZER_SYNC_TOKEN"]

    def test_select(self):
        import panzer
        self.fake()
        os.environ["PANZER_SYNC_TOKEN"] = FINE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                summary = Path(tmp) / "summary.md"
                self.assertEqual(panzer.main(["token-check", "--select", "celeris", "--summary", str(summary)]), 0)
                text = summary.read_text()
                self.assertIn("PanzerDevOrg/Celeris", text)
                self.assertNotIn("PanzerDevOrg/velox", text)
        finally:
            del os.environ["PANZER_SYNC_TOKEN"]


if __name__ == "__main__":
    unittest.main()
