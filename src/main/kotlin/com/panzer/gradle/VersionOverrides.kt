@file:Suppress("unused")

package com.panzer.gradle

import java.io.File

/**
 * Resolves the effective version block (`minecraft_version`, `neo_version`,
 * `neo_version_range`, `parchment_mappings_version`, ...) for a given mod, applying
 * `[overrides.<mod_id>."<mc_version>"]` on top of the base block `["<mc_version>"]`
 * field by field. A field missing from the override is inherited from the base
 * block -- the override never needs to repeat fields that don't change.
 */
object VersionOverrides {

    /**
     * Reads and parses [mergedToml] itself. Prefer [resolve] with already-parsed
     * tables when the caller already has them (e.g. [ModBuildProperties]) to avoid
     * reading the same TOML from disk again.
     */
    fun resolve(mergedToml: File, modId: String, mcVersion: String): Map<String, String> =
        resolve(TomlBlockReader.parse(mergedToml), modId, mcVersion)

    /** Resolves against tables already parsed by the caller -- no extra file I/O or parsing. */
    fun resolve(tables: List<TomlBlockReader.Table>, modId: String, mcVersion: String): Map<String, String> {
        val base = TomlBlockReader.find(tables, mcVersion)?.entries
            ?: error(
                "Base block [\"$mcVersion\"] does not exist in the merged TOML. " +
                        "Check that common.stonecutter.properties.toml declares that Minecraft version."
            )

        val override = TomlBlockReader.find(tables, "overrides", modId, mcVersion)?.entries
            ?: return base

        return base + override
    }
}
