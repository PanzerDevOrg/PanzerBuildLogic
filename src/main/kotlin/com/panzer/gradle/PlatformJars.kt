@file:Suppress("unused")

package com.panzer.gradle

import org.gradle.api.Project
import org.gradle.api.tasks.bundling.AbstractArchiveTask
import org.gradle.api.tasks.bundling.Zip

/**
 * Per-system copies of the mod jar, for people who want the explicit file for
 * their OS (GitHub releases), declared in the mod's TOML:
 *
 * ```toml
 * [publish]
 * platforms = ["windows", "linux", "macos", "java"]
 * ```
 *
 * Each `<os>Jar` task writes `<mod>-<version>-<os>.jar` next to the universal
 * jar in `build/libs/<mod version>/`: the same jar with only that OS's
 * `natives/<os>-<arch>/` directories (every architecture of it). `java` keeps
 * no native library at all, for the pure-Java fallbacks everywhere. Entries
 * keep the universal jar's order, so META-INF/MANIFEST.MF stays first.
 */
object PlatformJars {

    val OPERATING_SYSTEMS = listOf("windows", "linux", "macos")
    val SUPPORTED = OPERATING_SYSTEMS + "java"

    fun register(project: Project, platforms: List<String>, modVersion: String) {
        if (platforms.isEmpty()) {
            return
        }
        val unknown = platforms.filterNot { it in SUPPORTED }
        require(unknown.isEmpty()) { "[publish] platforms $unknown are not supported; use any of $SUPPORTED" }

        val jar = project.tasks.named(ModPackaging.releaseJar(project), AbstractArchiveTask::class.java)
        val outDir = project.rootProject.layout.buildDirectory.dir("libs/$modVersion")
        val tasks = platforms.map { platform ->
            project.tasks.register("${platform}Jar", Zip::class.java) {
                group = "build"
                description = if (platform == "java") "Mod jar without native libraries."
                else "Mod jar with only the $platform native libraries."
                dependsOn(jar)
                archiveBaseName.set(jar.flatMap { it.archiveBaseName })
                archiveVersion.set(jar.flatMap { it.archiveVersion })
                archiveClassifier.set(platform)
                archiveExtension.set("jar")
                destinationDirectory.set(outDir)
                from(project.zipTree(jar.flatMap { it.archiveFile })) {
                    if (platform == "java") {
                        exclude("natives/**")
                    } else {
                        OPERATING_SYSTEMS.filter { it != platform }.forEach { exclude("natives/$it-*/**") }
                    }
                }
            }
        }
        project.tasks.register("platformJars") {
            group = "build"
            description = "Builds the per-system jars declared in [publish] platforms."
            dependsOn(tasks)
        }
        project.tasks.matching { it.name == "buildAndCollect" }.configureEach { dependsOn("platformJars") }
    }

    /** `["a", "b"]` as read raw by [TomlBlockReader]. */
    fun parseList(raw: String?): List<String> {
        if (raw.isNullOrBlank()) return emptyList()
        return raw.trim().removePrefix("[").removeSuffix("]").split(',')
            .map { it.trim().removeSurrounding("\"").removeSurrounding("'") }
            .filter { it.isNotEmpty() }
    }
}
