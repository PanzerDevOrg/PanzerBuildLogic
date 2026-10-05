package com.panzer.gradle

import java.io.File

/**
 * Merges `../panzer-build-logic/common.stonecutter.properties.toml` with the
 * mod's `mod.stonecutter.properties.toml` into `stonecutter.properties.toml`,
 * which Stonecutter and the rest of the build read.
 *
 * Whole tables win: if the mod declares `[key]`, that table replaces the common
 * one entirely (keys are not mixed). That is what lets a mod override
 * `[stonecutter]` or a `["1.21.1"]` block, and its own
 * `[stonecutter.profiles.<name>]` replace a common one with the same name.
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
            val chosen = modBlocks[key] ?: commonBlocks[key] ?: continue
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
