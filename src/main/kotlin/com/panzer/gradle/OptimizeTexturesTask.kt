@file:Suppress("unused")

package com.panzer.gradle

import org.gradle.api.DefaultTask
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.file.RegularFileProperty
import org.gradle.api.provider.Property
import org.gradle.api.tasks.*
import org.gradle.process.ExecOperations
import org.gradle.work.DisableCachingByDefault
import javax.inject.Inject

// Rewrites PNGs in place via an external tool (oxipng); build-cache reuse would be
// unsafe and gains nothing, so caching is disabled explicitly (required by validatePlugins).
@DisableCachingByDefault(because = "Optimizes files in place using an external tool")
abstract class OptimizeTexturesTask @Inject constructor(
    private val execOperations: ExecOperations,
) : DefaultTask() {

    @get:InputFiles
    @get:PathSensitive(PathSensitivity.RELATIVE)
    @get:IgnoreEmptyDirectories
    @get:Optional
    abstract val resourcesDir: DirectoryProperty

    @get:Internal
    abstract val rootCacheDir: DirectoryProperty

    @get:Internal
    abstract val tools: Property<ToolVersions>

    /**
     * Receipt file written after a successful run, declared as this task's only
     * `@OutputFile`. oxipng optimizes PNGs in place (same dir as the input), so
     * without a declared output Gradle can never mark this task UP-TO-DATE and
     * would re-invoke the external oxipng process -- one of the slower steps in
     * the build -- on every single run, even when no texture changed. Wiring a
     * receipt file lets Gradle's normal input-snapshot comparison do its job:
     * same PNG bytes as last time + receipt still present -> skip the task
     * entirely instead of re-running oxipng over every texture.
     */
    @get:OutputFile
    abstract val optimizedMarker: RegularFileProperty

    @TaskAction
    fun run() {
        val spec = tools.get().get("oxipng")
        if (!spec.enabled) {
            logger.lifecycle("[optimizeTextures] Disabled ([tools.oxipng].enabled=false).")
            writeMarker("disabled")
            return
        }

        val pngs = resourcesDir.get().asFile.walkTopDown()
            .filter { it.isFile && it.extension.equals("png", ignoreCase = true) }
            .map { it.absolutePath }
            .toList()

        if (pngs.isEmpty()) {
            writeMarker("no-pngs")
            return
        }

        val resolver = ExternalToolResolver(
            execOperations = execOperations,
            rootCacheDir = rootCacheDir.get().asFile,
            logPrefix = "optimizeTextures",
        )

        val binary = resolver.resolve(spec, "oxipng") { target, extension, version ->
            "https://github.com/oxipng/oxipng/releases/download/v$version/oxipng-$version-$target.$extension"
        } ?: run {
            logger.warn("[optimizeTextures] Could not obtain oxipng binary. Skipping optimization.")
            writeMarker("no-binary")
            return
        }

        logger.lifecycle("[optimizeTextures] Optimizing {} PNG textures using oxipng v{}...", pngs.size, spec.requireVersion())

        // One process for every PNG instead of one process per PNG: oxipng batches
        // internally, so this avoids paying JVM-fork overhead pngs.size times over.
        execOperations.exec {
            commandLine(listOf(binary.absolutePath, "-o", "6", "--strip", "safe", "--alpha") + pngs)
        }

        writeMarker("optimized:${pngs.size}:${spec.requireVersion()}")
    }

    private fun writeMarker(status: String) {
        val marker = optimizedMarker.get().asFile
        marker.parentFile.mkdirs()
        marker.writeText(status)
    }
}
