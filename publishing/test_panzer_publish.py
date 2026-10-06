"""Offline tests for panzer_publish (python -m unittest discover publishing)."""
import json
import os
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

import panzer_publish as pp

TOML = textwrap.dedent('''
    [mod]
    id = "demo"
    name = "Demo"
    version = "1.2.0"
    side = "CLIENT"
    issues = "https://github.com/PanzerDevOrg/Demo/issues"

    [stonecutter]
    versions = ["1.21.1", "26.1"]
    vcs_version = "1.21.1"

    [publish]
    modrinth_id = "AAAA"
    curseforge_id = "123"
    curseforge_slug = "demo"
    platforms = ["windows", "linux", "java"]

    [publish.dependencies.celeris]
    type = "required"
    modrinth = "sQphaM3I"
    curseforge = "celeris"
    github = "PanzerDevOrg/Celeris"

    ["1.21.1"]
    minecraft_version = "1.21.1"
    game_versions = ["1.21", "1.21.1"]

    ["26.1"]
    minecraft_version = "26.1"
    game_versions = ["26.1", "26.2"]
''')

README = textwrap.dedent('''
    ![Banner](./docs/media/banner.png)

    **Demo** does things. See [the license](LICENSE) and [docs](https://example.com).
    Requires [Celeris](https://github.com/PanzerDevOrg/Celeris).

    <div align="center">
    <img alt="Shot" src="docs/media/shot.png" style="width:50%">
    </div>

    | Feature | Note |
    |:---|:---:|
    | Fast | yes |

    <!-- publish:off -->
    ## Building
    ./gradlew build
    <!-- publish:on -->

    ## Requirements
    Java 21.

    <!-- panzer:footer -->
    Made by **Panzer**
    <!-- /panzer:footer -->
''')


def make_mod(tmp: Path) -> Path:
    (tmp / "mod.stonecutter.properties.toml").write_text(TOML)
    (tmp / "README.md").write_text(README)
    (tmp / "docs" / "changelogs").mkdir(parents=True)
    (tmp / "docs" / "changelogs" / "1.2.0.md").write_text("# Changelog\n\n* Thing\n")
    libs = tmp / "build" / "libs" / "1.2.0"
    libs.mkdir(parents=True)
    for build in ("1.21.1", "26.1"):
        for suffix in ("", "-sources", "-windows", "-linux", "-java", "-dev"):
            with zipfile.ZipFile(libs / f"demo-1.2.0+{build}{suffix}.jar", "w") as z:
                z.writestr("x.txt", build + suffix)
    return tmp


class FakeCurseForge(pp.CurseForgeVersions):
    def __init__(self):
        types = [{"id": 1, "slug": "minecraft-1-21"}, {"id": 2, "slug": "modloader"},
                 {"id": 3, "slug": "java"}, {"id": 4, "slug": "environment"}]
        versions = [{"id": 10, "name": "1.21", "gameVersionTypeID": 1}, {"id": 11, "name": "1.21.1", "gameVersionTypeID": 1},
                    {"id": 20, "name": "NeoForge", "gameVersionTypeID": 2}, {"id": 30, "name": "Java 21", "gameVersionTypeID": 3},
                    {"id": 31, "name": "Java 25", "gameVersionTypeID": 3}, {"id": 40, "name": "Client", "gameVersionTypeID": 4},
                    {"id": 41, "name": "Server", "gameVersionTypeID": 4}]
        super().__init__(versions, types)


class Labels(unittest.TestCase):
    def test_range_label(self):
        self.assertEqual(pp.range_label(["1.21", "1.21.1", "1.21.6"]), "1.21(.0-.6)")
        self.assertEqual(pp.range_label(["1.21.7", "1.21.10"]), "1.21(.7-.10)")
        self.assertEqual(pp.range_label(["26.1", "26.3"]), "26(.1-.3)")
        self.assertEqual(pp.range_label(["1.21.11"]), "1.21.11")

    def test_java(self):
        self.assertEqual(pp.java_for("1.21.1"), 21)
        self.assertEqual(pp.java_for("26.1"), 25)


class Description(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = pp.load_config(make_mod(Path(self.tmp.name)))

    def tearDown(self):
        self.tmp.cleanup()

    def test_published_markdown(self):
        md = pp.published_markdown(README, self.cfg)
        self.assertNotIn("Building", md)
        self.assertIn("https://raw.githubusercontent.com/PanzerDevOrg/Demo/master/docs/media/banner.png", md)
        self.assertIn("https://raw.githubusercontent.com/PanzerDevOrg/Demo/master/docs/media/shot.png", md)
        self.assertIn("[the license](https://github.com/PanzerDevOrg/Demo/blob/master/LICENSE)", md)
        self.assertIn("[docs](https://example.com)", md)

    def test_panzer_markers_dropped_content_kept(self):
        md = pp.published_markdown(README, self.cfg)
        self.assertNotIn("panzer:footer", md)
        self.assertIn("Made by **Panzer**", md)

    def test_dependency_links_per_site(self):
        self.assertIn("[Celeris](https://modrinth.com/mod/sQphaM3I)", pp.published_markdown(README, self.cfg, "modrinth"))
        self.assertIn("[Celeris](https://www.curseforge.com/minecraft/mc-mods/celeris)",
                      pp.published_markdown(README, self.cfg, "curseforge"))

    def test_range_must_match_game_versions(self):
        root = Path(self.tmp.name)
        bad = (root / "mod.stonecutter.properties.toml").read_text().replace(
            'game_versions = ["1.21", "1.21.1"]', 'game_versions = ["1.21", "1.21.1"]\nminecraft_version_range = "[1.21.1,1.21.2)"')
        (root / "mod.stonecutter.properties.toml").write_text(bad)
        with self.assertRaises(pp.PublishError):
            pp.load_config(root)

    def test_curseforge_html(self):
        h = pp.curseforge_html(pp.published_markdown(README, self.cfg))
        self.assertNotIn("<div", h)
        self.assertNotIn("width:50%", h)
        self.assertIn('<img alt="Shot" src="https://raw.githubusercontent.com/PanzerDevOrg/Demo/master/docs/media/shot.png"/>', h)
        self.assertIn('<th style="text-align:center">', h)
        self.assertIn('rel="noopener nofollow"', h)
        self.assertIn("<table>", h)

    def test_lint(self):
        warnings = pp.lint_readme(README)
        self.assertTrue(any("style=" in w for w in warnings))


class Plan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = make_mod(Path(self.tmp.name))
        self.cfg = pp.load_config(root)
        self.plan = pp.build_plan(self.cfg, root / "build" / "libs", "v1.2.0")

    def tearDown(self):
        self.tmp.cleanup()

    def test_files(self):
        f = self.plan["files"][0]
        self.assertEqual(f["version_number"], "1.2.0-1.21(.0-.1)")
        self.assertEqual(f["display_name"], "Demo 1.2.0 for Minecraft 1.21–1.21.1")
        self.assertTrue(f["universal"].endswith("demo-1.2.0+1.21.1.jar"))
        self.assertEqual(sorted(f["platforms"]), ["java", "linux", "windows"])
        self.assertTrue(f["sources"].endswith("-sources.jar"))
        self.assertEqual(self.plan["files"][1]["java"], 25)

    def test_check_offline(self):
        self.assertEqual(pp.check_plan(self.plan, online=False), [])
        bad = dict(self.plan, tag="v9.9.9")
        self.assertTrue(any("does not match" in e for e in pp.check_plan(bad, online=False)))

    def test_sides(self):
        self.assertEqual(self.plan["mod"]["client_side"], "required")
        self.assertEqual(self.plan["mod"]["server_side"], "unsupported")
        self.assertEqual(self.plan["curseforge"]["environments"], ["Client"])

    def test_modrinth_payload(self):
        data = pp.modrinth_payload(self.plan, self.plan["files"][0])
        self.assertEqual(data["dependencies"], [{"project_id": "sQphaM3I", "dependency_type": "required"}])
        self.assertEqual(data["loaders"], ["neoforge"])
        self.assertEqual(data["version_type"], "release")

    def test_curseforge_ids_and_payload(self):
        errors = []
        cf = FakeCurseForge()
        ids = cf.ids(self.plan["files"][0], self.plan, errors)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(ids), [10, 11, 20, 30, 40])
        payload = pp.curseforge_payload(self.plan, self.plan["files"][0], ids)
        self.assertEqual(payload["relations"], {"projects": [{"slug": "celeris", "type": "requiredDependency"}]})
        self.assertEqual(payload["changelogType"], "markdown")
        # 26.x is unknown to this fake CurseForge: reported, never silently dropped.
        cf.ids(self.plan["files"][1], self.plan, errors)
        self.assertTrue(any("'26.1'" in e for e in errors))

    def test_github_body_and_preview(self):
        body = pp.github_release_body(self.plan)
        self.assertIn("demo-1.2.0+1.21.1-windows.jar", body)
        self.assertIn("https://www.curseforge.com/minecraft/mc-mods/demo", body)
        page = pp.preview_html(self.plan, pp.published_markdown(README, self.cfg), "<p>cf</p>")
        self.assertIn("release preview (not published)", page)
        self.assertNotIn("-dev.jar", json.dumps(self.plan))

    def test_dry_run_publish(self):
        out = Path(self.tmp.name) / "out"
        rc = pp.main(["publish", "--mod", self.tmp.name, "--tag", "v1.2.0", "--out", str(out),
                      "--dry-run", "--offline"])
        self.assertEqual(rc, 0)
        self.assertIn("demo-1.2.0+26.1-java.jar", (out / "SHA256SUMS.txt").read_text())
        self.assertFalse(Path("SHA256SUMS.txt").exists(), "nothing is written outside --out")
        self.assertTrue((out / "preview.html").exists())
        self.assertIn("modrinth: create Demo 1.2.0", (out / "publish.log").read_text())

    def test_missing_site_token_skips_that_site(self):
        out = Path(self.tmp.name) / "out-real"
        calls = []
        saved = pp.publish_github, {k: os.environ.pop(k, None) for k in pp.SITE_TOKENS.values()}
        pp.publish_github = lambda plan, dry, log, out: calls.append("github")
        try:
            rc = pp.main(["publish", "--mod", self.tmp.name, "--tag", "v1.2.0", "--out", str(out), "--offline"])
        finally:
            pp.publish_github = saved[0]
            for k, v in saved[1].items():
                if v is not None:
                    os.environ[k] = v
        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["github"])
        log = (out / "publish.log").read_text()
        self.assertIn("modrinth: MODRINTH_TOKEN is not available to this repository, skipped", log)
        self.assertIn("targets=curseforge", log)


if __name__ == "__main__":
    unittest.main()
