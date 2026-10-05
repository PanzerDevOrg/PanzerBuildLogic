@file:Suppress("unused")

package com.panzer.gradle

import java.util.Locale

/**
 * A native library a mod ships inside its jar, declared under
 * `[natives.<name>]` in `mod.stonecutter.properties.toml`:
 *
 * ```toml
 * [natives.celeris_physics]
 * cmake_dir = "native"            # CMake project building it; omit for prebuilt binaries
 * headers = "native/include"      # optional: published as the `native-headers` artifact
 * platforms = ["linux-x86_64", "linux-aarch64", "windows-x86_64", "macos-x86_64", "macos-aarch64"]
 * ```
 *
 * Binaries live in the mod at `natives/<os>/<arch>/<file>` (committed, or
 * produced by `buildNative<Name>` for the host), the same layout every mod's
 * `collectNatives` already stages into the jar as `natives/<os>-<arch>/<file>`.
 */
data class NativeLibrarySpec(
    val name: String,
    val cmakeDir: String?,
    val headersDir: String?,
    val platforms: List<NativePlatform>,
) {
    val taskSuffix: String
        get() = name.split('_', '-').joinToString("") { part -> part.replaceFirstChar { it.uppercase() } }

    /** `libfoo.so` / `foo.dll` / `libfoo.dylib`, matching CMake's default shared-library naming. */
    fun fileName(platform: NativePlatform): String = when (platform.os) {
        "windows" -> "$name.dll"
        "macos" -> "lib$name.dylib"
        else -> "lib$name.so"
    }

    companion object {
        private val SUPPORTED = setOf(
            "linux-x86_64", "linux-aarch64", "linux-ppc64le", "linux-riscv64",
            "windows-x86_64", "windows-aarch64", "macos-x86_64", "macos-aarch64",
        )

        fun from(tables: List<TomlBlockReader.Table>): Map<String, NativeLibrarySpec> =
            tables.asSequence()
                .filter { it.path.size == 2 && it.path[0] == "natives" }
                .associate { table ->
                    val name = table.path[1]
                    val platforms = parseArray(table.entries["platforms"]).ifEmpty { listOf(NativePlatform.host().classifier) }
                    val unknown = platforms.filterNot { it in SUPPORTED }
                    require(unknown.isEmpty()) {
                        "[natives.$name] platforms $unknown are not supported; use any of $SUPPORTED"
                    }
                    name to NativeLibrarySpec(
                        name = name,
                        cmakeDir = table.entries["cmake_dir"]?.takeIf { it.isNotBlank() },
                        headersDir = table.entries["headers"]?.takeIf { it.isNotBlank() },
                        platforms = platforms.map(NativePlatform::parse),
                    )
                }

        /** `["a", "b"]` as read raw by [TomlBlockReader]. */
        private fun parseArray(raw: String?): List<String> {
            if (raw.isNullOrBlank()) return emptyList()
            return raw.trim().removePrefix("[").removeSuffix("]").split(',')
                .map { it.trim().removeSurrounding("\"").removeSurrounding("'") }
                .filter { it.isNotEmpty() }
        }
    }
}

data class NativePlatform(val os: String, val arch: String) {
    val classifier: String get() = "$os-$arch"

    /** Source location inside the mod: `natives/<os>/<arch>/`. */
    val sourcePath: String get() = "$os/$arch"

    companion object {
        fun parse(classifier: String): NativePlatform =
            NativePlatform(classifier.substringBefore('-'), classifier.substringAfter('-'))

        fun host(): NativePlatform {
            val os = System.getProperty("os.name").lowercase(Locale.ROOT)
            val arch = System.getProperty("os.arch").lowercase(Locale.ROOT)
            return NativePlatform(
                when {
                    os.contains("win") -> "windows"
                    os.contains("mac") -> "macos"
                    else -> "linux"
                },
                when {
                    arch.contains("aarch64") || arch.contains("arm64") -> "aarch64"
                    arch.contains("ppc64le") -> "ppc64le"
                    arch.contains("riscv64") -> "riscv64"
                    else -> "x86_64"
                },
            )
        }
    }
}
