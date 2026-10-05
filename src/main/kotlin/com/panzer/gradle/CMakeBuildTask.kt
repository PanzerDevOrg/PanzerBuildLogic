@file:Suppress("unused")

package com.panzer.gradle

import org.gradle.api.DefaultTask
import org.gradle.api.file.DirectoryProperty
import org.gradle.api.file.RegularFileProperty
import org.gradle.api.provider.ListProperty
import org.gradle.api.provider.Property
import org.gradle.api.tasks.*
import org.gradle.process.ExecOperations
import java.io.File
import javax.inject.Inject

/**
 * Configures, builds and (optionally) tests a CMake project for the host
 * platform, then copies the resulting shared library to [outputLibrary]
 * (`natives/<os>/<arch>/<file>` in the mod, where `collectNatives` picks it
 * up). Cross-platform binaries come from CI runners of each OS, not from here.
 *
 * Cacheable: the output depends only on the CMake sources and arguments, so
 * an unchanged native tree is never recompiled, locally or in CI.
 */
@CacheableTask
abstract class CMakeBuildTask @Inject constructor(
    private val execOperations: ExecOperations,
) : DefaultTask() {

    @get:InputDirectory
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val sourceDir: DirectoryProperty

    @get:Input
    abstract val target: Property<String>

    @get:Input
    abstract val buildType: Property<String>

    @get:Input
    abstract val cmakeArgs: ListProperty<String>

    /** Run `ctest` after building (the project's own native tests). */
    @get:Input
    abstract val runTests: Property<Boolean>

    @get:Internal
    abstract val buildDir: DirectoryProperty

    @get:OutputFile
    abstract val outputLibrary: RegularFileProperty

    @TaskAction
    fun build() {
        val build = buildDir.get().asFile
        val cmake = findOnPath("cmake")
            ?: throw org.gradle.api.GradleException(
                "cmake not found on PATH: install CMake >= 3.16 and a C++20 compiler to build " +
                        "${target.get()}, or use the committed binaries (skip -Ppanzer.native.build)."
            )
        execOperations.exec {
            commandLine(listOf(cmake, "-S", sourceDir.get().asFile.absolutePath, "-B", build.absolutePath,
                "-DCMAKE_BUILD_TYPE=${buildType.get()}") + cmakeArgs.get())
        }
        execOperations.exec {
            commandLine(cmake, "--build", build.absolutePath, "--config", buildType.get(), "--parallel")
        }
        if (runTests.get()) {
            findOnPath("ctest")?.let { ctest ->
                execOperations.exec {
                    commandLine(ctest, "--test-dir", build.absolutePath, "-C", buildType.get(), "--output-on-failure")
                }
            }
        }
        val fileName = outputLibrary.get().asFile.name
        val built = build.walkTopDown().firstOrNull { it.isFile && it.name == fileName }
            ?: throw org.gradle.api.GradleException("CMake build of ${target.get()} produced no $fileName under $build")
        val out = outputLibrary.get().asFile
        out.parentFile.mkdirs()
        built.copyTo(out, overwrite = true)
        logger.lifecycle("[panzer.natives] ${target.get()} -> ${out.path}")
    }

    private fun findOnPath(exe: String): String? {
        val names = if (System.getProperty("os.name").lowercase().contains("win")) listOf("$exe.exe", exe) else listOf(exe)
        return System.getenv("PATH").orEmpty().split(File.pathSeparator)
            .flatMap { dir -> names.map { File(dir, it) } }
            .firstOrNull { it.isFile && it.canExecute() }
            ?.absolutePath
    }
}
