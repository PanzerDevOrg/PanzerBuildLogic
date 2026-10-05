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



class TokenCheck(unittest.TestCase):
    """panzer_token with GitHub's answers simulated."""

    def setUp(self):
        import panzer_token
        self.pt = panzer_token
        self.saved = (panzer_token.api, panzer_token.git_service)

    def tearDown(self):
        self.pt.api, self.pt.git_service = self.saved

    def fake(self, user_status=200, repo_status=200, upload=200, receive=200, headers=None):
        R = self.pt.Response
        self.pt.api = lambda token, path: (R(user_status, headers or {}, "", {"login": "bichal"}) if path == "/user"
                                           else R(repo_status, {}, "Not Found" if repo_status == 404 else ""))
        self.pt.git_service = lambda token, repo, svc: R(upload if svc == "git-upload-pack" else receive, {})

    def test_good_fine_grained(self):
        self.fake(headers={"github-authentication-token-expiration": "2027-01-01 00:00:00 UTC"})
        lines, ok = self.pt.check("github_pat_x", ["PanzerDevOrg/A"])
        self.assertTrue(ok)
        text = "\n".join(lines)
        self.assertIn("fine-grained", text)
        self.assertIn("Acts as: bichal", text)
        self.assertIn("2027-01-01", text)
        self.assertIn("Workflows permission", text)

    def test_invalid(self):
        self.fake(user_status=401)
        lines, ok = self.pt.check("github_pat_x", ["PanzerDevOrg/A"])
        self.assertFalse(ok)
        self.assertIn("invalid or expired", lines[0])

    def test_read_only(self):
        self.fake(receive=403)
        lines, ok = self.pt.check("github_pat_x", ["PanzerDevOrg/A"])
        self.assertFalse(ok)
        self.assertIn("Contents: Read and write", "\n".join(lines))

    def test_repository_not_selected(self):
        self.fake(repo_status=404, upload=404, receive=404)
        lines, ok = self.pt.check("github_pat_x", ["PanzerDevOrg/velox"])
        self.assertFalse(ok)
        self.assertIn("select it under Repository access", "\n".join(lines))

    def test_classic_scopes(self):
        self.fake(headers={"x-oauth-scopes": "repo"})
        lines, ok = self.pt.check("ghp_x", ["PanzerDevOrg/A"])
        self.assertFalse(ok)
        self.assertIn("missing scopes: workflow", "\n".join(lines))
        self.fake(headers={"x-oauth-scopes": "repo, workflow"})
        self.assertTrue(self.pt.check("ghp_x", ["PanzerDevOrg/A"])[1])


if __name__ == "__main__":
    unittest.main()
