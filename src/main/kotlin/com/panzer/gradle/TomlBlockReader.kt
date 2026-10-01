@file:Suppress("unused")

package com.panzer.gradle

import java.io.File
import java.io.IOException

/**
 * Reads `[table]` blocks from the already-merged TOML, without touching the
 * Stonecutter extension. Exists because [PanzerDiagnostics] runs from
 * `settings.gradle.kts`, before `StonecutterBuildExtension` is available as a
 * project extension, and because resolving `[overrides.<mod_id>."<version>"]`
 * needs to inspect tables that Stonecutter doesn't expose directly (they're our
 * own data, not Stonecutter's).
 *
 * Supports only what this build-logic needs: single-line string/bool/number
 * values inside `[a.b.c]` or `[a.b."c.d"]` tables with quoted segments. Not a
 * general-purpose TOML parser.
 */
object TomlBlockReader {

    data class Table(val path: List<String>, val entries: Map<String, String>)

    private val TABLE_HEADER = Regex("""^\[([^]]+)]\s*$""")
    private val KEY_VALUE = Regex("""^([A-Za-z0-9_.-]+)\s*=\s*(.+)$""")

    fun parse(file: File): List<Table> {
        if (!file.exists()) return emptyList()

        val tables = mutableListOf<Table>()
        var currentPath: List<String>? = null
        var currentEntries = mutableMapOf<String, String>()

        fun flush() {
            currentPath?.let { tables += Table(it, currentEntries) }
        }

        for (rawLine in readLinesWithRetry(file)) {
            val line = rawLine.trim()
            if (line.isEmpty() || line.startsWith("#")) continue

            val header = TABLE_HEADER.find(line)
            if (header != null) {
                flush()
                currentPath = splitPath(header.groupValues[1])
                currentEntries = mutableMapOf()
                continue
            }

            val kv = KEY_VALUE.find(line) ?: continue
            currentEntries[kv.groupValues[1]] = unquote(kv.groupValues[2].trim())
        }
        flush()

        return tables
    }

    /**
     * Reads [file] with a short retry-with-backoff on [IOException]. This file is
     * `stonecutter.properties.toml`, generated moments earlier by `settings.gradle.kts`
     * in the same Gradle invocation and then re-opened here from a later phase
     * ([ModBuildProperties] during project configuration). Gradle itself guarantees
     * that ordering (settings always fully runs before any project configures), so
     * a failure here isn't an ordering bug -- it's almost always a transient OS-level
     * lock on a just-written file, most commonly on Windows when antivirus real-time
     * scanning briefly opens a freshly created file right after it's closed. That
     * explains the "build fails, immediate re-run succeeds with no changes" pattern:
     * by the second run the lock is long gone. Retrying here for a few hundred
     * milliseconds absorbs that window instead of failing the whole build over it.
     */
    private fun readLinesWithRetry(file: File, attempts: Int = 5, initialDelayMillis: Long = 20): List<String> {
        var delay = initialDelayMillis
        var lastError: IOException? = null
        repeat(attempts) { attempt ->
            try {
                return file.readLines()
            } catch (e: IOException) {
                lastError = e
                if (attempt < attempts - 1) {
                    Thread.sleep(delay)
                    delay *= 2
                }
            }
        }
        throw lastError ?: IOException("Failed reading ${file.path}")
    }

    /** Returns the table whose exact path matches, or null if none does. */
    fun find(tables: List<Table>, vararg path: String): Table? =
        tables.firstOrNull { it.path == path.toList() }

    /**
     * Splits a table header into segments, respecting single or double quotes
     * as in `overrides.tessera."1.21.1"` -> ["overrides", "tessera", "1.21.1"].
     */
    private fun splitPath(header: String): List<String> {
        val segments = mutableListOf<String>()
        val current = StringBuilder()
        var inQuotes = false
        var quoteChar = ' '

        for (c in header) {
            when {
                inQuotes && c == quoteChar -> inQuotes = false
                !inQuotes && (c == '"' || c == '\'') -> {
                    inQuotes = true
                    quoteChar = c
                }
                !inQuotes && c == '.' -> {
                    segments += current.toString()
                    current.clear()
                }
                else -> current.append(c)
            }
        }
        segments += current.toString()
        return segments.map { it.trim() }
    }

    private fun unquote(value: String): String {
        val trimmed = value.trim()
        return when {
            trimmed.startsWith("\"") && trimmed.endsWith("\"") && trimmed.length >= 2 ->
                trimmed.substring(1, trimmed.length - 1)
            trimmed.startsWith("'") && trimmed.endsWith("'") && trimmed.length >= 2 ->
                trimmed.substring(1, trimmed.length - 1)
            else -> trimmed
        }
    }
}
