package com.panzer.gradle

import org.gradle.api.DefaultTask
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.provider.Property
import org.gradle.api.tasks.CacheableTask
import org.gradle.api.tasks.Input
import org.gradle.api.tasks.InputDirectory
import org.gradle.api.tasks.OutputDirectory
import org.gradle.api.tasks.PathSensitive
import org.gradle.api.tasks.PathSensitivity
import org.gradle.api.tasks.TaskAction

/**
 * Runs [JvmModulePreprocessor] over one `[jvm.modules.<name>]` source
 * directory for one JDK major version.
 *
 * Deliberately its own task (not folded into `compile<Name>Java`) so
 * Gradle's up-to-date checking is keyed on `javaMajor` + the source
 * directory's content -- if two Stonecutter version subprojects both
 * require Java 21, this only actually re-runs once, not once per
 * subproject, because both point at the same output directory under
 * `rootProject.layout.buildDirectory` (see `panzer.neoforge-mod` wiring).
 */
@CacheableTask
abstract class PreprocessJvmModuleTask : DefaultTask() {

    @get:InputDirectory
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val sourceDir: DirectoryProperty

    @get:Input
    abstract val javaMajor: Property<Int>

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @TaskAction
    fun run() {
        JvmModulePreprocessor.processDirectory(
            sourceDir.get().asFile,
            outputDir.get().asFile,
            javaMajor.get(),
        )
    }
}
