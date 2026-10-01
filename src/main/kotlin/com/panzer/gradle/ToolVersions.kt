@file:Suppress("unused")

package com.panzer.gradle

import java.io.File

/**
 * Catalog of external tools declared under `[tools.<name>]` in the merged TOML
 * (version, enabled, plus any extra key via [rawEntries]). Tool versions live in
 * the common TOML, so adding or bumping a tool needs no build-logic recompile.
 */
data class ToolSpec(
    val name: String,
    val version: String?,
    val enabled: Boolean,
    val rawEntries: Map<String, String>,
) {
    fun requireVersion(): String =
        version ?: error(
            "[tools.$name] does not declare 'version' in the merged TOML. " +
                    "Add 'version = \"x.y.z\"' under [tools.$name] in common.stonecutter.properties.toml, " +
                    "or in the mod's own table if it needs to override it."
        )

    fun prop(key: String, default: String = ""): String = rawEntries[key] ?: default
}

class ToolVersions private constructor(private val specs: Map<String, ToolSpec>) {

    fun get(name: String): ToolSpec =
        specs[name] ?: ToolSpec(name = name, version = null, enabled = true, rawEntries = emptyMap())

    fun all(): Collection<ToolSpec> = specs.values

    companion object {
        private const val PREFIX = "tools."

        /**
         * Builds from a file, parsing it. Prefer [from] with already-parsed tables
         * when the caller already has them (e.g. [ModBuildProperties]) -- this file
         * gets read and parsed independently once per call, which adds up when it's
         * one of several readers hitting the same TOML during project configuration.
         */
        fun from(mergedToml: File): ToolVersions = from(TomlBlockReader.parse(mergedToml))

        /** Builds from tables already parsed by the caller -- no extra file I/O or parsing. */
        fun from(tables: List<TomlBlockReader.Table>): ToolVersions {
            val specs = tables
                .filter { it.path.size == 2 && it.path[0] == "tools" }
                .associate { table ->
                    val name = table.path[1]
                    name to ToolSpec(
                        name = name,
                        version = table.entries["version"],
                        enabled = table.entries["enabled"]?.toBooleanStrictOrNull() ?: true,
                        rawEntries = table.entries,
                    )
                }
            return ToolVersions(specs)
        }
    }
}
