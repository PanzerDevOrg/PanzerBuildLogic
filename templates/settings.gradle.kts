import java.io.File
import java.util.Collections.emptyList

pluginManagement {
    // Two ways to get panzer-build-logic (see README, "Two ways a mod can consume
    // this repo"): a sibling checkout at "../panzer-build-logic" is used directly via
    // includeBuild when present (instant local iteration, no publish step -- this is
    // what our own repos use day to day), and a public, unauthenticated Maven repo
    // published by panzer-build-logic's own CI is the fallback for anyone without that
    // sibling folder (external contributors, a fresh CI checkout, anyone outside
    // PanzerDevOrg's own multi-repo layout).
    val localBuildLogic = File(rootDir, "../panzer-build-logic")
    val usingLocalBuildLogic = localBuildLogic.exists()

    if (usingLocalBuildLogic) {
        includeBuild(localBuildLogic)
    }

    repositories {
        gradlePluginPortal()
        mavenCentral()
        maven("https://maven.kikugie.dev/releases") { name = "KikuGie Releases" }
        maven("https://maven.kikugie.dev/snapshots") { name = "KikuGie Snapshots" }
        maven("https://maven.fabricmc.net/") { name = "FabricMC" }
        if (!usingLocalBuildLogic) {
            maven("https://panzerdevorg.github.io/PanzerBuildLogic/maven") { name = "PanzerBuildLogic" }
        }
    }

    if (!usingLocalBuildLogic) {
        // includeBuild substitution doesn't need a version (it always resolves to
        // whatever's on disk); resolving from a real Maven repo does. Centralizing it
        // here means every "id(\"panzer.neoforge-mod\")" call site across every mod's
        // build.gradle.kts stays exactly as-is, version-less, in both modes.
        val panzerBuildLogicVersion = providers.gradleProperty("panzerBuildLogicVersion").getOrElse("0.1.0")
        resolutionStrategy {
            eachPlugin {
                if (requested.id.id == "panzer.neoforge-mod" || requested.id.id == "panzer.neoforge-mod-example") {
                    useVersion(panzerBuildLogicVersion)
                }
            }
        }
    }
}

plugins {
    id("dev.kikugie.stonecutter") version "0.9.7"
}

data class TomlTable(val path: List<String>, val entries: Map<String, String>)

fun splitTomlPath(header: String): List<String> {
    val segments = mutableListOf<String>()
    val current = StringBuilder()
    var inQuotes = false
    var quoteChar = ' '
    for (c in header) {
        when {
            inQuotes && c == quoteChar -> inQuotes = false
            !inQuotes && (c == '"' || c == '\'') -> { inQuotes = true; quoteChar = c }
            !inQuotes && c == '.' -> { segments += current.toString(); current.clear() }
            else -> current.append(c)
        }
    }
    segments += current.toString()
    return segments.map { it.trim() }
}

fun unquoteToml(value: String): String {
    val t = value.trim()
    return if ((t.startsWith("\"") && t.endsWith("\"") || t.startsWith("'") && t.endsWith("'")) && t.length >= 2) {
        t.substring(1, t.length - 1)
    } else t
}

/** Extracts a bracketed, comma-separated array value, e.g. `["1.21.1", "1.21.3"]` -> the two entries. Null if `value` isn't a bracketed array. */
fun tomlArrayOrNull(value: String): List<String>? {
    val t = value.trim()
    if (!t.startsWith("[") || !t.endsWith("]")) return null
    val content = t.substring(1, t.length - 1).trim()
    if (content.isEmpty()) return emptyList()
    return content.split(",").map { unquoteToml(it.trim()) }
}

fun parseToml(file: File): List<TomlTable> {
    if (!file.exists()) return emptyList()
    val tableHeader = Regex("""^\[([^]]+)]\s*$""")
    val keyValue = Regex("""^([A-Za-z0-9_.-]+)\s*=\s*(.+)$""")
    val tables = mutableListOf<TomlTable>()
    var currentPath: List<String>? = null
    var currentEntries = mutableMapOf<String, String>()

    fun flush() { currentPath?.let { tables += TomlTable(it, currentEntries) } }

    for (rawLine in file.readLines()) {
        val line = rawLine.trim()
        if (line.isEmpty() || line.startsWith("#")) continue
        val header = tableHeader.find(line)
        if (header != null) {
            flush()
            currentPath = splitTomlPath(header.groupValues[1])
            currentEntries = mutableMapOf()
            continue
        }
        val kv = keyValue.find(line) ?: continue
        // Keep array values ("[...]") as-is (unquoted below only unwraps a single quoted
        // scalar) so tomlArrayOrNull can still parse them later; a bare scalar still gets
        // its surrounding quotes stripped like before.
        val rawValue = kv.groupValues[2].trim()
        currentEntries[kv.groupValues[1]] = if (rawValue.startsWith("[")) rawValue else unquoteToml(rawValue)
    }
    flush()
    return tables
}

val modRoot = rootDir
val commonToml = File(modRoot, "../panzer-build-logic/common.stonecutter.properties.toml")
val modToml = File(modRoot, "mod.stonecutter.properties.toml")
val mergedToml = File(modRoot, "stonecutter.properties.toml")

// Declare both source TOMLs as configuration-cache inputs. Reads through plain
// File APIs here are not reliably fingerprinted, which let an edited TOML build
// with stale values from the cache; providers.fileContents() is always tracked.
listOf(modToml, commonToml).filter { it.exists() }.forEach {
    providers.fileContents(layout.rootDirectory.file(it.relativeTo(rootDir).invariantSeparatorsPath)).asBytes.get()
}

// Merge first (in-memory + write to disk) so the version list below and the
// diagnostics pass further down both read from one resolved set of tables.
val mergedTables: List<TomlTable> = run {
    val commonTables = parseToml(commonToml)
    val modTables = parseToml(modToml)
    val modPaths = modTables.map { it.path }.toSet()
    val merged = commonTables.filter { it.path !in modPaths } + modTables

    val rendered = buildString {
        for (table in merged) {
            val header = table.path.joinToString(".") { segment ->
                if (segment.any { it.isDigit() } && segment.contains(".")) "\"$segment\"" else segment
            }
            appendLine("[$header]")
            for ((key, value) in table.entries) {
                val isArray = value.startsWith("[")
                val needsQuotes = !isArray && value.toBooleanStrictOrNull() == null && value.toLongOrNull() == null
                appendLine(if (needsQuotes) "$key = \"$value\"" else "$key = $value")
            }
            appendLine()
        }
    }

    // Only touch the file when its content actually changed. This runs on every
    // settings evaluation (every single Gradle invocation), and rewriting it
    // unconditionally -- even byte-for-byte identical -- bumps its mtime/content
    // hash every time. Any task that declares stonecutter.properties.toml as an
    // @InputFile (and Gradle's own file-system-watching layer) would then see it
    // as "changed" on every build and could never be UP-TO-DATE.
    if (!mergedToml.exists() || mergedToml.readText() != rendered) {
        mergedToml.writeText(rendered)
    }

    merged
}

fun tableAt(vararg path: String): TomlTable? = mergedTables.firstOrNull { it.path == path.toList() }

/**
 * Resolves the list of versions to create as Stonecutter subprojects.
 *
 * Priority: explicit `[stonecutter] versions` (the original behavior) ->
 * `[stonecutter] profile = "<name>"` resolved against `[stonecutter.profiles.<name>]`
 * (+ optional `extra_versions` / `exclude_versions`) -> error if neither is present.
 * Finally, `-Pstonecutter.versions=a,b,c` (if passed) narrows down to that subset.
 */
fun resolveVersions(): List<String> {
    val stonecutterTable = tableAt("stonecutter")
        ?: error("[stonecutter] block is missing in stonecutter.properties.toml.")

    fun cliOverride(resolved: List<String>): List<String> {
        val raw = startParameter.projectProperties["stonecutter.versions"] ?: return resolved
        val requested = raw.split(",").map { it.trim() }.filter { it.isNotEmpty() }
        val unknown = requested.filterNot { it in resolved }
        if (unknown.isNotEmpty()) {
            error(
                "-Pstonecutter.versions requested $unknown but only $resolved are available " +
                        "(check 'versions'/'profile' in stonecutter.properties.toml)."
            )
        }
        return requested
    }

    stonecutterTable.entries["versions"]?.let { raw ->
        val explicit = tomlArrayOrNull(raw) ?: error(
            "[stonecutter] versions = $raw is not a bracketed array, e.g. [\"1.21.1\", \"1.21.3\"]."
        )
        return cliOverride(explicit)
    }

    val profileName = stonecutterTable.entries["profile"]
        ?: error("[stonecutter] must declare either 'versions' or 'profile' in stonecutter.properties.toml.")
    val profileTable = tableAt("stonecutter", "profiles", profileName)
        ?: error(
            "[stonecutter] declares profile = \"$profileName\" but no [stonecutter.profiles.$profileName] " +
                    "block was found (checked this mod's own TOML and panzer-build-logic's common TOML)."
        )
    val base = profileTable.entries["versions"]?.let { tomlArrayOrNull(it) }
        ?: error("[stonecutter.profiles.$profileName] has no 'versions' array.")

    val extra = stonecutterTable.entries["extra_versions"]?.let { tomlArrayOrNull(it) }.orEmpty()
    val exclude = stonecutterTable.entries["exclude_versions"]?.let { tomlArrayOrNull(it) }.orEmpty()
    val resolved = (base + extra).distinct().filterNot { it in exclude }
    if (resolved.isEmpty()) {
        error("Resolved version list for profile \"$profileName\" is empty after 'exclude_versions'.")
    }
    return cliOverride(resolved)
}

val scVersions = resolveVersions()
val scVcsVersion = tableAt("stonecutter")?.entries?.get("vcs_version")
    ?: error("[stonecutter] must declare 'vcs_version' in stonecutter.properties.toml.")

stonecutter {
    create(rootProject) {
        versions(scVersions)
        vcsVersion = scVcsVersion
    }
}

run {
    val tables = mergedTables
    val errors = mutableListOf<String>()
    val warnings = mutableListOf<String>()

    val modTable = tables.firstOrNull { it.path == listOf("mod") }?.entries.orEmpty()
    for (key in listOf("id", "version", "group", "name", "package")) {
        if (modTable[key].isNullOrBlank()) errors += "Missing 'mod.$key' in [mod]."
    }

    // Validation uses scVersions directly, the same list stonecutter { versions(...) }
    // creates subprojects from, so the two cannot drift out of sync.
    val activeVersions = scVersions
    if (activeVersions.isEmpty()) errors += "No active version was resolved (see 'versions'/'profile' in [stonecutter])."

    val requiredVersionFields = listOf(
        "minecraft_version", "minecraft_version_range",
        "neo_version", "neo_version_range", "parchment_mappings_version",
    )
    val rangePattern = Regex("""^[\[(][^,]*,[^,]*[])]$""")

    for (version in activeVersions) {
        val block = tables.firstOrNull { it.path == listOf(version) }
        if (block == null) {
            errors += "Active version \"$version\" has no [\"$version\"] block in either the mod's TOML or the common one."
            continue
        }
        for (field in requiredVersionFields) {
            val value = block.entries[field]
            if (value.isNullOrBlank()) {
                errors += "[\"$version\"] does not declare '$field'."
            } else if (field.endsWith("_range") && !rangePattern.matches(value)) {
                errors += "[\"$version\"].$field = \"$value\" is not shaped like a Maven range (e.g. \"[21.1,21.2)\")."
            }
        }
    }

    for (table in tables.filter { it.path.size == 3 && it.path[0] == "overrides" }) {
        val version = table.path[2]
        if (tables.none { it.path == listOf(version) }) {
            warnings += "[overrides.${table.path[1]}.\"$version\"] references a version with no base block -- it will never apply."
        }
    }

    for (table in tables.filter { it.path.size == 3 && it.path[0] == "jvm" && it.path[1] == "modules" }) {
        val moduleName = table.path[2]
        if (!modRoot.resolve("src/$moduleName/java").exists()) {
            warnings += "[jvm.modules.$moduleName] is declared but 'src/$moduleName/java' does not exist -- it will be skipped."
        }
    }

    for (table in tables.filter { it.path.size == 2 && it.path[0] == "tools" }) {
        if (table.entries["version"].isNullOrBlank()) {
            warnings += "[tools.${table.path[1]}] does not declare 'version' -- it will fail if any task requires it."
        }
    }

    if (warnings.isNotEmpty()) {
        println("[panzer-diagnostics] ${modRoot.name} -- ${warnings.size} warning(s):")
        warnings.forEach { println("  ! $it") }
    }
    if (errors.isNotEmpty()) {
        val body = errors.joinToString("\n") { "  - $it" }
        throw GradleException(
            "[panzer-diagnostics] ${modRoot.name} -- ${errors.size} configuration error(s) in stonecutter.properties.toml:\n$body"
        )
    }
}
