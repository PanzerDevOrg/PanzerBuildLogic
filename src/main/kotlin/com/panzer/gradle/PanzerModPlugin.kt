package com.panzer.gradle

import org.gradle.api.Plugin
import org.gradle.api.Project

/**
 * `plugins { id("panzer.mod") }`: the build plugin of a mod built for more than
 * one loader. Each Stonecutter node applies the plugin of its loader:
 * panzer.fabric-mod on `<version>-fabric` nodes, panzer.neoforge-mod on the
 * others. Loader-specific build script code goes through
 * `pluginManager.withPlugin("net.neoforged.moddev") { ... }` (or Loom's id).
 */
class PanzerModPlugin : Plugin<Project> {
    override fun apply(project: Project) {
        val fabric = project.name.endsWith("-${PanzerSettingsPlugin.FABRIC}")
        project.pluginManager.apply(if (fabric) "panzer.fabric-mod" else "panzer.neoforge-mod")
    }
}
