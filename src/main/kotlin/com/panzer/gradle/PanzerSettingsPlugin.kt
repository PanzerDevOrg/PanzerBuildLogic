package com.panzer.gradle

import dev.kikugie.stonecutter.settings.StonecutterSettingsExtension
import org.gradle.api.Plugin
import org.gradle.api.initialization.Settings
import java.io.File

/**
 * `plugins { id("panzer.settings") }`: everything a mod's settings.gradle.kts
 * used to do by hand, so that file is a few identical lines in every mod.
 *
 * 1. Merges `../panzer-build-logic/common.stonecutter.properties.toml` with the
 *    mod's `mod.stonecutter.properties.toml` into `stonecutter.properties.toml`
 *    ([TomlMerge], whole tables win).
 * 2. Applies Stonecutter and the Foojay toolchain resolver (versions pinned by
 *    `[plugins]` in the common TOML, through this build's dependencies).
 * 3. Resolves the Minecraft versions to build from `[stonecutter]`:
 *    `versions = [...]`, or `profile = "<name>"` from `[stonecutter.profiles.<name>]`
 *    plus optional `extra_versions` / `exclude_versions`; then
 *    `-Pstonecutter.versions=a,b` narrows that list for one run.
 * 4. Creates the Stonecutter tree for the root project plus every project in
 *    `[stonecutter] branches = ["example"]`, and names the root project after
 *    `[mod] name`.
 * 5. Validates the merged TOML ([PanzerDiagnostics]) and reports every problem
 *    at once, before any project configures.
 */
class PanzerSettingsPlugin : Plugin<Settings> {

    override fun apply(settings: Settings) {
        val rootDir = settings.rootDir
        val commonToml = File(rootDir, "../panzer-build-logic/common.stonecutter.properties.toml")
        val modToml = File(rootDir, "mod.stonecutter.properties.toml")
        val mergedToml = File(rootDir, "stonecutter.properties.toml")

        if (!commonToml.exists()) {
            error("[panzer.settings] '${commonToml.normalize().path}' was not found. Clone PanzerBuildLogic next to " +
                    "this project as 'panzer-build-logic'.")
        }
        if (!modToml.exists()) {
            error("[panzer.settings] 'mod.stonecutter.properties.toml' was not found in '${rootDir.path}'.")
        }

        // Declare both sources as configuration-cache inputs: plain File reads are
        // not reliably fingerprinted, which let an edited TOML build with stale
        // values from the cache. providers.fileContents() is always tracked.
        for (source in listOf(modToml, commonToml)) {
            settings.providers.fileContents(
                settings.layout.rootDirectory.file(source.relativeTo(rootDir).invariantSeparatorsPath)
            ).asBytes.get()
        }

        TomlMerge.writeIfChanged(mergedToml, TomlMerge.merge(commonToml.readLines(), modToml.readLines()))
        val tables = TomlBlockReader.parse(mergedToml)

        settings.pluginManager.apply("org.gradle.toolchains.foojay-resolver-convention")
        settings.pluginManager.apply("dev.kikugie.stonecutter")

        val stonecutterTable = TomlBlockReader.find(tables, "stonecutter")
            ?: error("[panzer.settings] [stonecutter] is missing in stonecutter.properties.toml.")
        val configuredVcs = stonecutterTable.entries["vcs_version"]
            ?: error("[panzer.settings] [stonecutter] must declare 'vcs_version'.")
        val scVersions = resolveVersions(settings, tables, stonecutterTable)
        // Stonecutter needs the vcs version registered; when a subset leaves it
        // out, the active version (always registered) stands in for it.
        val vcsVersion = if (configuredVcs in scVersions) configuredVcs else activeVersion(settings) ?: scVersions.first()
        val branches = PlatformJars.parseList(stonecutterTable.entries["branches"])

        branches.forEach { settings.include(":$it") }
        val projects: List<Any> = listOf(settings.rootProject) + branches.map { settings.project(":$it") }
        settings.extensions.getByType(StonecutterSettingsExtension::class.java).create(projects) {
            versions(scVersions)
            this.vcsVersion.set(vcsVersion)
        }

        TomlBlockReader.find(tables, "mod")?.entries?.get("name")?.takeIf { it.isNotBlank() }?.let {
            settings.rootProject.name = it
        }

        PanzerDiagnostics.validate(rootDir, mergedToml, scVersions).renderAndMaybeFail(rootDir.name)
    }

    /**
     * The Minecraft versions to register:
     *
     * - declared: `[stonecutter] versions`, or the profile's versions plus
     *   `extra_versions` minus `exclude_versions`;
     * - narrowed by `-Pstonecutter.versions=a,b` or, outside CI, by the local
     *   `.panzer/versions` file `panzer versions use` writes. Either may also
     *   name a profile version the mod excludes (to try a port);
     * - always including the active Stonecutter version, which Stonecutter
     *   requires to be registered.
     */
    private fun resolveVersions(
        settings: Settings,
        tables: List<TomlBlockReader.Table>,
        stonecutter: TomlBlockReader.Table,
    ): List<String> {
        val explicit = PlatformJars.parseList(stonecutter.entries["versions"])
        val available: List<String>
        val declared: List<String>
        if (explicit.isNotEmpty()) {
            available = explicit
            declared = explicit
        } else {
            val profile = stonecutter.entries["profile"]
                ?: error("[panzer.settings] [stonecutter] must declare either 'versions' or 'profile'.")
            val profileTable = TomlBlockReader.find(tables, "stonecutter", "profiles", profile)
                ?: error("[panzer.settings] [stonecutter] profile = \"$profile\" but no " +
                        "[stonecutter.profiles.$profile] exists in the mod's TOML or the common one.")
            val base = PlatformJars.parseList(profileTable.entries["versions"])
                .ifEmpty { error("[panzer.settings] [stonecutter.profiles.$profile] has no 'versions' array.") }
            val exclude = PlatformJars.parseList(stonecutter.entries["exclude_versions"]).toSet()
            available = (base + PlatformJars.parseList(stonecutter.entries["extra_versions"])).distinct()
            declared = available.filterNot { it in exclude }
                .ifEmpty { error("[panzer.settings] profile \"$profile\" is empty after 'exclude_versions'.") }
        }

        val cli = settings.startParameter.projectProperties["stonecutter.versions"]
        val local = if (cli == null && System.getenv("CI") != "true") localSubset(settings) else null
        val raw = cli ?: local ?: return withActive(settings, declared, available)
        val requested = raw.split(",").map { it.trim() }.filter { it.isNotEmpty() }
        val unknown = requested.filterNot { it in available }
        if (unknown.isNotEmpty()) {
            val origin = if (cli != null) "-Pstonecutter.versions" else ".panzer/versions"
            error("[panzer.settings] $origin asks for $unknown, but this mod can build $available.")
        }
        val selected = withActive(settings, available.filter { it in requested }, available)
        if (selected != declared) {
            val origin = if (cli != null) "-Pstonecutter.versions" else ".panzer/versions (panzer versions all: every version)"
            println("[panzer] Building $selected of $declared ($origin).")
        }
        return selected
    }

    private fun withActive(settings: Settings, versions: List<String>, available: List<String>): List<String> {
        val active = activeVersion(settings) ?: return versions
        if (active in versions || active !in available) return versions
        println("[panzer] Also registering $active: it is Stonecutter's active version (panzer versions use <v> switches it).")
        return available.filter { it in versions || it == active }
    }

    /** `stonecutter active "<v>"` from stonecutter.gradle.kts. */
    private fun activeVersion(settings: Settings): String? {
        val script = settings.layout.rootDirectory.file("stonecutter.gradle.kts")
        val text = settings.providers.fileContents(script).asText.orNull ?: return null
        return Regex("""^stonecutter active "([^"]+)"""", RegexOption.MULTILINE).find(text)?.groupValues?.get(1)
    }

    /** `.panzer/versions` (git-ignored): the versions this checkout works on. */
    private fun localSubset(settings: Settings): String? {
        val file = settings.layout.rootDirectory.file(".panzer/versions")
        return settings.providers.fileContents(file).asText.orNull
            ?.lines()?.map { it.substringBefore('#').trim() }?.filter { it.isNotEmpty() }
            ?.joinToString(",")?.takeIf { it.isNotEmpty() }
    }
}
