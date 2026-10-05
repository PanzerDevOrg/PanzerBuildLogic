package com.panzer.gradle

import java.io.File

/**
 * Merges `../panzer-build-logic/common.stonecutter.properties.toml` with the
 * mod's `mod.stonecutter.properties.toml` into `stonecutter.properties.toml`,
 * which Stonecutter and the rest of the build read.
 *
 * Whole tables win: if the mod declares `[key]`, that table replaces the common
 * one entirely (keys are not mixed). That is what lets a mod override
 * `[stonecutter]` or `[legal]`, and its own `[stonecutter.profiles.<name>]`
 * replace a common one with the same name.
 *
 * Minecraft version blocks (`["1.21.1"]`) are the exception: they merge key by
 * key. The common block holds what is the same for every mod (Minecraft and
 * NeoForge builds, Parchment), so all mods share one download per version; the
 * mod's block adds or overrides keys, typically what it claims to support
 * (`minecraft_version_range`, `neo_version_range`, `game_versions`).
 * Lines are kept verbatim, comments included.
 */
object TomlMerge {

    private val TABLE_HEADER = Regex("""^\[(.+)]\s*$""")

    fun merge(common: List<String>, mod: List<String>): String {
        val commonBlocks = splitBlocks(common)
        val modBlocks = splitBlocks(mod)
        val keys = LinkedHashSet<String>().apply {
            addAll(commonBlocks.keys)
            addAll(modBlocks.keys)
        }
        val merged = StringBuilder()
        for (key in keys) {
            val mod = modBlocks[key]
            val common = commonBlocks[key]
            val chosen = when {
                mod != null && common != null && VERSION_HEADER.matches(key) -> mergeKeys(common, mod)
                else -> mod ?: common ?: continue
            }
            merged.append(chosen.joinToString("\n")).append("\n")
        }
        return merged.toString().trimEnd() + "\n"
    }

    /**
     * Writes [text] to [target] only when it differs: this runs on every Gradle
     * invocation, and rewriting identical content would invalidate the
     * configuration cache and every task that reads the file.
     */
    fun writeIfChanged(target: File, text: String): Boolean {
        if (target.exists() && target.readText() == text) return false
        target.writeText(text)
        return true
    }

    private val VERSION_HEADER = Regex(""""?\d+(\.\d+)+(-[\w.]+)?"?""")
    private val KEY = Regex("""^\s*([A-Za-z0-9_.-]+)\s*=""")

    /** Common block with the mod's keys replacing or added to it (comments travel with their key). */
    private fun mergeKeys(common: List<String>, mod: List<String>): List<String> {
        val result = common.toMutableList()
        while (result.isNotEmpty() && result.last().isBlank()) result.removeAt(result.size - 1)
        val pending = mutableListOf<String>()
        for (line in mod.drop(1)) {
            val key = KEY.find(line)?.groupValues?.get(1)
            if (key == null) {
                if (line.isNotBlank()) pending += line
                continue
            }
            val index = result.indexOfFirst { KEY.find(it)?.groupValues?.get(1) == key }
            if (index >= 0) {
                result[index] = line
                result.addAll(index, pending.filterNot { it in result })
            } else {
                result += pending
                result += line
            }
            pending.clear()
        }
        return result + ""
    }

    private fun splitBlocks(lines: List<String>): LinkedHashMap<String, MutableList<String>> {
        val blocks = LinkedHashMap<String, MutableList<String>>()
        var current = ""
        blocks[current] = mutableListOf()
        for (line in lines) {
            TABLE_HEADER.find(line.trim())?.let { current = it.groupValues[1].trim() }
            blocks.getOrPut(current) { mutableListOf() }.add(line)
        }
        if (blocks[""]?.all { it.isBlank() } == true) blocks.remove("")
        return blocks
    }
}
