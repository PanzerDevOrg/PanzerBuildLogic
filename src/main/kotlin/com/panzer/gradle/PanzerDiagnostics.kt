@file:Suppress("unused")

package com.panzer.gradle

import java.io.File

/**
 * Validates a mod's merged TOML before any Gradle project exists (called from
 * `settings.gradle.kts`, right after the merge). Collects every problem found
 * and fails once with a complete report, instead of making the developer fix
 * and re-run Gradle one error at a time.
 *
 * Does not replace the individual `error(...)` calls [ModBuildProperties] already
 * has for single keys -- those remain the safety net at project-configuration
 * time. This is an earlier pass: cheaper and more thorough.
 */
object PanzerDiagnostics {

    private val REQUIRED_TOP_LEVEL_KEYS = listOf(
        "mod.id", "mod.version", "mod.group", "mod.name", "mod.package",
    )

    private val REQUIRED_VERSION_FIELDS = listOf(
        "minecraft_version", "minecraft_version_range",
        "neo_version", "neo_version_range",
        "parchment_mappings_version",
    )

    private val VERSION_RANGE_PATTERN = Regex("""^[\[(][^,]*,[^,]*[])]$""")

    class Report(val errors: List<String>, val warnings: List<String>) {
        val hasErrors: Boolean get() = errors.isNotEmpty()

        fun renderAndMaybeFail(modDisplayName: String) {
            if (warnings.isNotEmpty()) {
                println("[panzer-diagnostics] $modDisplayName -- ${warnings.size} warning(s):")
                warnings.forEach { println("  ! $it") }
            }
            if (hasErrors) {
                val body = errors.joinToString("\n") { "  - $it" }
                error(
                    "[panzer-diagnostics] $modDisplayName -- ${errors.size} configuration error(s) " +
                            "in stonecutter.properties.toml:\n$body"
                )
            }
        }
    }

    fun validate(modRoot: File, mergedToml: File, activeVersions: List<String>): Report {
        val errors = mutableListOf<String>()
        val warnings = mutableListOf<String>()

        if (!mergedToml.exists()) {
            errors += "Could not find '${mergedToml.path}'. Did settings.gradle.kts run the merge?"
            return Report(errors, warnings)
        }

        val tables = TomlBlockReader.parse(mergedToml)
        @Suppress("UnusedVariable")
        val flatKeys = tables.associate { table ->
            table.path.joinToString(".") to table.entries
        }

        val modTable = tables.firstOrNull { it.path == listOf("mod") }?.entries.orEmpty()
        for (key in REQUIRED_TOP_LEVEL_KEYS) {
            val field = key.substringAfter("mod.")
            if (modTable[field].isNullOrBlank()) {
                errors += "Missing '$key' in table [mod]."
            }
        }

        if (activeVersions.isEmpty()) {
            errors += "[stonecutter] does not declare any active version."
        }

        for (version in activeVersions) {
            val block = TomlBlockReader.find(tables, version)
            if (block == null) {
                errors += "Active version \"$version\" has no [\"$version\"] block " +
                        "in either the mod's TOML or the common one."
                continue
            }
            for (field in REQUIRED_VERSION_FIELDS) {
                val value = block.entries[field]
                if (value.isNullOrBlank()) {
                    errors += "[\"$version\"] does not declare '$field'."
                    continue
                }
                if (field.endsWith("_range") && !VERSION_RANGE_PATTERN.matches(value)) {
                    errors += "[\"$version\"].$field = \"$value\" is not shaped like a Maven range " +
                            "(e.g. \"[21.1,21.2)\")."
                }
            }
        }

        val overrideTables = tables.filter { it.path.size == 3 && it.path[0] == "overrides" }
        @Suppress("DestructuringDeclaration")
        for (table in overrideTables) {
            val (_, targetModId, version) = table.path
            if (TomlBlockReader.find(tables, version) == null) {
                warnings += "[overrides.$targetModId.\"$version\"] references version \"$version\" " +
                        "with no base block [\"$version\"] -- this override will never apply."
            }
        }

        val jvmModuleTables = tables.filter {
            it.path.size == 3 && it.path[0] == "jvm" && it.path[1] == "modules"
        }
        @Suppress("DestructuringDeclaration")
        for (table in jvmModuleTables) {
            val moduleName = table.path[2]
            val srcDir = modRoot.resolve("src/$moduleName/java")
            if (!srcDir.exists()) {
                warnings += "[jvm.modules.$moduleName] is declared but 'src/$moduleName/java' does not exist " +
                        "-- this module will be skipped in the build."
            }
        }

        val toolTables = tables.filter { it.path.size == 2 && it.path[0] == "tools" }
        @Suppress("DestructuringDeclaration")
        for (table in toolTables) {
            val toolName = table.path[1]
            if (table.entries["version"].isNullOrBlank()) {
                warnings += "[tools.$toolName] does not declare 'version' -- it will resolve to null " +
                        "and fail if any task requires it."
            }
        }

        return Report(errors, warnings)
    }
}
