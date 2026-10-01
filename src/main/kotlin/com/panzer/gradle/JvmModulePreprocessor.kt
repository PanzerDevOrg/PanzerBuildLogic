package com.panzer.gradle

import java.io.File

/**
 * Minimal standalone preprocessor for `[jvm.modules.*]` source sets (`ffm`,
 * `vector`, ...). These vary by **JDK major version**, not by Minecraft
 * version, so they can't reuse `stonecutterGenerate` directly -- that would
 * re-run the same substitution once per active MC version instead of once
 * per distinct JDK actually in use (today: 21 and 25).
 *
 * Mirrors Stonecutter's own comment-block syntax as closely as possible, so
 * existing blocks in this codebase keep working unchanged. A block opens
 * with a condition line and closes with an end marker; between them sits
 * the "true" branch, optionally followed by an else branch. Two shapes of
 * else are supported, and the shape is decided by whether the else-line
 * itself ends in an opening brace:
 *
 * Long form -- else with an opening brace, an explicit closing marker, any
 * number of lines in either branch. The inactive branch's lines are either
 * each individually prefixed with a plain line-comment marker, or the
 * whole branch is wrapped in a single block comment (opening marker on its
 * own line, closing marker on its own line, single- or multi-line body in
 * between).
 *
 * Short form -- else with no opening brace: exactly the *one* line
 * immediately following is the inactive branch (individually
 * line-commented, or wrapped inline in a block comment on that same
 * line), and the block ends right there -- no closing marker follows.
 * This mirrors Stonecutter's own one-line-else shorthand. Because it's
 * inherently single-line, it must never be used for a multi-line
 * replacement -- use the long form instead.
 *
 * A block with no else at all is also valid: just the opening condition
 * line, the "true" branch, and the closing marker.
 *
 * The condition is evaluated against the numeric JDK major version (21, 25,
 * ...), not a Minecraft version string -- deliberately a different axis from
 * Stonecutter's own MC-version conditions that may also appear (harmlessly
 * inert here) in the same file for MC-version differences, if a module ever
 * needs both.
 *
 * The preprocessor detects which comment style the non-selected branch
 * used and un-wraps it when that branch is the one selected; the
 * non-selected branch is simply dropped, comments and all.
 */
object JvmModulePreprocessor {

    private val CONDITION = Regex("""^\s*//\?\s*(>=|<=|>|<|==)\s*(\d+)\s*\{\s*""")
    // The else-line's own shape decides long form vs. short form: a
    // trailing "{" means long form (explicit "//?}" closes it later); no
    // trailing "{" means short form (exactly the next line is the branch).
    private val ELSE_LONG = Regex("""^\s*//\?}\s*else\s*\{\s*""")
    private val ELSE_SHORT = Regex("""^\s*//\?}\s*else\s*""")
    private val END_LINE = Regex("""^\s*//\?}\s*""")
    private val BLOCK_COMMENT_OPEN = Regex("""^\s*/\*.*""")
    private val BLOCK_COMMENT_CLOSE = Regex(""".*\*/\s*""")
    // A line consisting of nothing but a "//" line-comment prefix (used to
    // mark every line of an inactive branch individually, Stonecutter-style).
    private val SLASH_SLASH_PREFIX = Regex("""^(\s*)//(.*)$""")

    /**
     * Preprocesses [source] for [javaMajor], returning the transformed text.
     * Lines outside any `//? <op><digits> {` block pass through unchanged.
     */
    fun process(source: String, javaMajor: Int): String {
        val lines = source.lines()
        val out = StringBuilder()
        var i = 0

        while (i < lines.size) {
            val line = lines[i]
            val match = CONDITION.matchEntire(line)
            if (match == null) {
                out.append(line).append('\n')
                i++
                continue
            }

            val (op, digits) = match.destructured
            val threshold = digits.toInt()
            val conditionTrue = when (op) {
                ">=" -> javaMajor >= threshold
                "<=" -> javaMajor <= threshold
                ">" -> javaMajor > threshold
                "<" -> javaMajor < threshold
                "==" -> javaMajor == threshold
                else -> error("Unreachable operator '$op'")
            }

            // Collect the "true" branch body up to the else-line (either
            // shape) or a bare "//?}" (no-else form).
            i++
            val trueBranch = mutableListOf<String>()
            while (i < lines.size && !ELSE_LONG.matches(lines[i]) && !ELSE_SHORT.matches(lines[i]) && !END_LINE.matches(lines[i])) {
                trueBranch.add(lines[i])
                i++
            }
            if (i >= lines.size) {
                error("Unterminated //? block (missing '//?}' or '//?} else')")
            }

            val falseBranch = mutableListOf<String>()
            when {
                ELSE_LONG.matches(lines[i]) -> {
                    i++ // consume "//?} else {"
                    while (i < lines.size && !END_LINE.matches(lines[i])) {
                        falseBranch.add(lines[i])
                        i++
                    }
                    if (i >= lines.size) {
                        error("Unterminated //? ... } else { block (missing closing '//?}')")
                    }
                    i++ // consume closing "//?}"
                }
                ELSE_SHORT.matches(lines[i]) -> {
                    i++ // consume "//?} else"
                    if (i >= lines.size) {
                        error("//? ... } else (short form) has no following line to use as its branch")
                    }
                    falseBranch.add(lines[i])
                    i++ // consume that one line; no closing "//?}" for short form
                }
                else -> {
                    // Bare "//?}", no else at all.
                    i++
                }
            }

            val selected = if (conditionTrue) trueBranch else falseBranch
            out.append(unwrapBranch(selected).joinToString("\n"))
            if (selected.isNotEmpty()) out.append('\n')
        }

        return out.toString()
    }

    /**
     * Un-comments a selected branch, auto-detecting which of the two
     * supported inert-comment styles it used:
     *   - a block comment -- same line or spanning several, or
     *   - every line individually prefixed with `//`.
     * A branch already containing live (uncommented) code -- e.g. the
     * "true" branch is often written live, only the "false" branch
     * commented -- is returned unchanged.
     */
    private fun unwrapBranch(branch: List<String>): List<String> {
        if (branch.isEmpty()) return branch

        // Single-line block comment: "/* code */" all on one line.
        if (branch.size == 1) {
            val l = branch[0]
            val openIdx = l.indexOf("/*")
            val closeIdx = l.lastIndexOf("*/")
            if (openIdx in 0..<closeIdx) {
                val before = l.substring(0, openIdx)
                val inner = l.substring(openIdx + 2, closeIdx).trim()
                val after = l.substring(closeIdx + 2)
                return listOf(before + inner + after)
            }
        }

        // Multi-line block comment: first line opens with "/*", last line
        // closes with "*/" (each may carry code alongside the marker on
        // the same line, e.g. "/*" alone, or "code */").
        val firstIsOpen = BLOCK_COMMENT_OPEN.matches(branch.first())
        val lastIsClose = BLOCK_COMMENT_CLOSE.matches(branch.last())
        if (firstIsOpen && lastIsClose && branch.size >= 2) {
            val firstStripped = branch.first().replaceFirst("/*", "")
            val lastStripped = branch.last().let {
                val idx = it.lastIndexOf("*/")
                it.substring(0, idx) + it.substring(idx + 2)
            }
            val middle = branch.subList(1, branch.size - 1)
            val result = mutableListOf<String>()
            if (firstStripped.isNotBlank()) result.add(firstStripped)
            result.addAll(middle)
            if (lastStripped.isNotBlank()) result.add(lastStripped)
            return result
        }

        // Every non-blank line individually "//"-prefixed -> strip one
        // leading "//" from each (preserving any indentation before it).
        val nonBlank = branch.filter { it.isNotBlank() }
        val allSlashPrefixed = nonBlank.isNotEmpty() && nonBlank.all { SLASH_SLASH_PREFIX.matches(it) }
        if (allSlashPrefixed) {
            return branch.map { l ->
                val m = SLASH_SLASH_PREFIX.matchEntire(l) ?: return@map l
                val (indent, rest) = m.destructured
                // Drop one optional space right after the "//" too, matching
                // how these lines are normally authored ("// code" -> "code").
                indent + rest.removePrefix(" ")
            }
        }

        return branch
    }

    /**
     * Preprocesses every `.java` file under [srcDir] into [outDir] for
     * [javaMajor], preserving relative paths. [outDir] is cleared first so
     * stale generated files never linger after a source file is removed.
     */
    fun processDirectory(srcDir: File, outDir: File, javaMajor: Int) {
        outDir.deleteRecursively()
        outDir.mkdirs()
        srcDir.walkTopDown().filter { it.isFile }.forEach { file ->
            val relative = file.relativeTo(srcDir)
            val target = File(outDir, relative.path)
            target.parentFile.mkdirs()
            if (file.extension == "java") {
                target.writeText(process(file.readText(), javaMajor))
            } else {
                file.copyTo(target, overwrite = true)
            }
        }
    }
}
