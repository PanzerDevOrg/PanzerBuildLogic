package com.panzer.gradle

import dev.kikugie.stonecutter.controller.StonecutterControllerExtension
import org.gradle.api.Plugin
import org.gradle.api.Project

/**
 * `plugins { id("panzer.stonecutter") }` in a mod's stonecutter.gradle.kts:
 * applies the Stonecutter controller and the parameters every mod shares, so
 * that file only keeps the `stonecutter active "<version>"` line Stonecutter
 * itself rewrites when the active version changes.
 *
 * Swaps `mod_version` / `minecraft` (string literals for the version being
 * built) and constants `release` (false only for the "template" mod id),
 * `fabric` and `neoforge` (the node's loader: `<version>-fabric` nodes build
 * for Fabric), so loader-specific code reads `//? if fabric {`.
 */
class PanzerStonecutterPlugin : Plugin<Project> {

    override fun apply(project: Project) {
        project.pluginManager.apply("dev.kikugie.stonecutter")

        val tables = TomlBlockReader.parse(project.file("stonecutter.properties.toml"))
        fun mod(key: String): String = TomlBlockReader.find(tables, "mod")?.entries?.get(key)
            ?: error("[panzer.stonecutter] '$key' is missing under [mod] in stonecutter.properties.toml.")
        val modVersion = mod("version")
        val modId = mod("id")

        project.extensions.getByType(StonecutterControllerExtension::class.java).parameters {
            swaps.put("mod_version", "\"$modVersion\";")
            swaps.put("minecraft", "\"${node.metadata.version}\";")
            constants.put("release", modId != "template")
            val fabric = node.metadata.project.endsWith("-${PanzerSettingsPlugin.FABRIC}")
            constants.put(PanzerSettingsPlugin.FABRIC, fabric)
            constants.put(PanzerSettingsPlugin.NEOFORGE, !fabric)
        }
    }
}
