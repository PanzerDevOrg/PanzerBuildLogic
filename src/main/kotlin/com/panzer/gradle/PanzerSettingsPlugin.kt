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
        val scVersions = resolveVersions(settings, tables, stonecutterTable)
        val vcsVersion = stonecutterTable.entries["vcs_version"]
            ?: error("[panzer.settings] [stonecutter] must declare 'vcs_version'.")
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

    private fun resolveVersions(
        settings: Settings,
        tables: List<TomlBlockReader.Table>,
        stonecutter: TomlBlockReader.Table,
    ): List<String> {
        val explicit = PlatformJars.parseList(stonecutter.entries["versions"])
        val resolved = explicit.ifEmpty {
            val profile = stonecutter.entries["profile"]
                ?: error("[panzer.settings] [stonecutter] must declare either 'versions' or 'profile'.")
            val profileTable = TomlBlockReader.find(tables, "stonecutter", "profiles", profile)
                ?: error("[panzer.settings] [stonecutter] profile = \"$profile\" but no " +
                        "[stonecutter.profiles.$profile] exists in the mod's TOML or the common one.")
            val base = PlatformJars.parseList(profileTable.entries["versions"])
                .ifEmpty { error("[panzer.settings] [stonecutter.profiles.$profile] has no 'versions' array.") }
            val extra = PlatformJars.parseList(stonecutter.entries["extra_versions"])
            val exclude = PlatformJars.parseList(stonecutter.entries["exclude_versions"]).toSet()
            (base + extra).distinct().filterNot { it in exclude }
                .ifEmpty { error("[panzer.settings] profile \"$profile\" is empty after 'exclude_versions'.") }
        }

        val raw = settings.startParameter.projectProperties["stonecutter.versions"] ?: return resolved
        val requested = raw.split(",").map { it.trim() }.filter { it.isNotEmpty() }
        val unknown = requested.filterNot { it in resolved }
        if (unknown.isNotEmpty()) {
            error("[panzer.settings] -Pstonecutter.versions requested $unknown but only $resolved are available.")
        }
        if (requested.size != resolved.size) {
            println("[stonecutter] Building subset $requested of $resolved (-Pstonecutter.versions override).")
        }
        return requested
    }
}
