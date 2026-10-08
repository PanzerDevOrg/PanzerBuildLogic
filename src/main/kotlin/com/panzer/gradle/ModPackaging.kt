package com.panzer.gradle

import org.gradle.api.file.DuplicatesStrategy
import org.gradle.api.GradleException
import org.gradle.api.Project
import org.gradle.api.tasks.Copy
import org.gradle.api.tasks.SourceSetContainer
import org.gradle.api.tasks.bundling.Jar
import org.gradle.api.tasks.bundling.ZipEntryCompression
import org.gradle.language.jvm.tasks.ProcessResources
import java.io.File

/**
 * What every mod's build.gradle.kts used to repeat, driven by the merged TOML:
 *
 * - [resources]: `META-INF/neoforge.mods.toml` and `*.mixins.json` expansion, and
 *   the legal files (LICENSE, LICENSE-*, NOTICE written by `panzer sync` from
 *   panzer-build-logic/legal) in every jar and sources jar under `META-INF/`.
 * - [jars]: shared jar settings and `buildAndCollect`.
 * - [dependencies]: `[depends_on.<id>]` Maven repositories and dependencies.
 * - [natives]: `collectNatives`, staging `natives/<os>/<arch>/` into the jar.
 */
object ModPackaging {

    /** Root-level files copied into `META-INF/` of every jar and sources jar. */
    fun legalFiles(project: Project): List<File> =
        project.rootDir.listFiles { file ->
            file.isFile && (file.name == "LICENSE" || file.name.startsWith("LICENSE-") || file.name == "NOTICE")
        }.orEmpty().sortedBy { it.name }

    /**
     * Template values for `neoforge.mods.toml`: `mod_<key>` for every `[mod]`
     * key, `mod_license` from `[legal] code` (falling back to `[mod] license`),
     * `loader_version_range`, `neo_version_range`, `minecraft_version_range`,
     * and `<id>_version` / `<id>_version_range` for every `[depends_on.<id>]`.
     */
    fun modsTomlProperties(project: Project, props: ModBuildProperties, tables: List<TomlBlockReader.Table>): Map<String, String> =
        buildMap {
            TomlBlockReader.find(tables, "mod")?.entries?.forEach { (key, value) -> put("mod_$key", value) }
            put("mod_version", props.modVersion)
            val license = TomlBlockReader.find(tables, "legal")?.entries?.get("code")
                ?: TomlBlockReader.find(tables, "mod")?.entries?.get("license")
                ?: throw GradleException("[panzer] Declare the code license as [legal] code (SPDX id).")
            put("mod_license", license)
            put("loader_version_range", props.req(project, "neoforge.loader_version_range"))
            put("neo_version_range", props.req(project, "neo_version_range"))
            put("minecraft_version_range", props.req(project, "minecraft_version_range"))
            for (table in tables.filter { it.path.size == 2 && it.path[0] == "depends_on" }) {
                val id = table.path[1]
                table.entries["version"]?.let { put("${id}_version", it) }
                table.entries["version_range"]?.let {
                    put("${id}_version_range", it)
                    put("${id}_fabric_version_range", fabricRange(it))
                }
            }
            // fabric.mod.json: the same ranges as Fabric version predicates.
            put("fabric_minecraft_version_range", fabricRange(props.req(project, "minecraft_version_range")))
            if (props.isFabric) {
                put("fabric_loader_version", props.fabricLoaderVersion)
                put("fabric_api_version", props.fabricApiVersion)
            }
        }

    /**
     * A Maven version range as a Fabric version predicate: `[1.21,1.21.7)` is
     * `>=1.21 <1.21.7`, `[1.21.11]` is `1.21.11`, `[0.2.1,)` is `>=0.2.1`.
     */
    fun fabricRange(maven: String): String {
        val raw = maven.trim()
        if (raw.isEmpty() || raw == "*") return "*"
        if (raw[0] != '[' && raw[0] != '(') return raw
        val open = raw[0]
        val close = raw.last()
        val inner = raw.substring(1, raw.length - 1)
        if (',' !in inner) return inner.trim()
        val (low, high) = inner.split(',', limit = 2).map { it.trim() }
        val parts = buildList {
            if (low.isNotEmpty()) add((if (open == '[') ">=" else ">") + low)
            if (high.isNotEmpty()) add((if (close == ']') "<=" else "<") + high)
        }
        return parts.joinToString(" ").ifEmpty { "*" }
    }

    /** The jar a release ships: Loom's remapped jar for obfuscated Fabric builds, otherwise `jar`. */
    fun releaseJar(project: Project): String =
        if (project.tasks.names.contains("remapJar")) "remapJar" else "jar"

    fun releaseSourcesJar(project: Project): String =
        if (project.tasks.names.contains("remapSourcesJar")) "remapSourcesJar" else "sourcesJar"

    fun resources(project: Project, props: ModBuildProperties, tables: List<TomlBlockReader.Table>) {
        val legal = legalFiles(project)
        project.tasks.named("processResources", ProcessResources::class.java) {
            duplicatesStrategy = DuplicatesStrategy.EXCLUDE
            val values = modsTomlProperties(project, props, tables)
            values.forEach { (key, value) -> inputs.property(key, value) }
            filesMatching("META-INF/neoforge.mods.toml") { expand(values) }
            filesMatching("fabric.mod.json") { expand(values) }
            // Each loader's jar carries only its own metadata.
            if (props.isFabric) exclude("META-INF/neoforge.mods.toml") else exclude("fabric.mod.json")
            val mixinJava = "JAVA_${props.requiredJava.majorVersion}"
            inputs.property("mixin_java", mixinJava)
            filesMatching("*.mixins.json") { expand(mapOf("java" to mixinJava)) }
            from(legal) { into("META-INF") }
        }
        project.tasks.named("sourcesJar", Jar::class.java) {
            from(legal) { into("META-INF") }
        }
    }

    fun jars(project: Project, props: ModBuildProperties) {
        project.tasks.withType(Jar::class.java).configureEach {
            duplicatesStrategy = DuplicatesStrategy.EXCLUDE
            entryCompression = ZipEntryCompression.DEFLATED
            dependsOn("processResources")
            exclude("META-INF/*.SF", "META-INF/*.DSA", "META-INF/*.RSA")
            exclude("META-INF/maven/**", "META-INF/DEPENDENCIES", "META-INF/INDEX.LIST")
            exclude("**/*.kotlin_module", "**/*.kotlin_builtins")
        }

        val collect = project.tasks.register("buildAndCollect", Copy::class.java) {
            group = "build"
            description = "Builds the mod, sources and per-system jars into build/libs/<mod version>/."
            from(project.tasks.named(releaseJar(project)))
            from(project.tasks.named(releaseSourcesJar(project)))
            inputs.property("version", props.modVersion)
            into(project.rootProject.layout.buildDirectory.dir("libs/${props.modVersion}"))
        }
        // Publishing is never a side effect of building: mods that depend on this
        // one get a local build through an explicit `publishToMavenLocal`.
    }

    /**
     * `[depends_on.<id>]` with an `artifact` becomes a dependency resolved from
     * mavenLocal() first (a local `publishToMavenLocal` of that mod wins), then
     * from `maven`; both restricted to the artifact's group.
     *
     * ```toml
     * [depends_on.celeris]
     * version = "0.2.0"                  # compiled against
     * version_range = "[0.2.0,)"         # accepted at runtime (neoforge.mods.toml)
     * artifact = "com.panzer.mods:celeris-{mc}"
     * maven = "https://panzerdevorg.github.io/Celeris/maven"
     * ```
     *
     * `-Ppanzer.<id>.maven=<url>` (or the older `-P<id>MavenUrl`) points at
     * another repository, e.g. a local build/publish-repo.
     */
    fun dependencies(project: Project, props: ModBuildProperties, tables: List<TomlBlockReader.Table>) {
        val groups = mutableSetOf<String>()
        for (table in tables.filter { it.path.size == 2 && it.path[0] == "depends_on" }) {
            val id = table.path[1]
            val artifact = table.entries["artifact"]?.takeIf { it.isNotBlank() } ?: continue
            val version = table.entries["version"]
                ?: throw GradleException("[panzer] [depends_on.$id] declares 'artifact' but no 'version'.")
            val coordinates = artifact.replace("{mc}", props.currentVersion)
            val group = coordinates.substringBefore(':')
            if (groups.add(group)) {
                project.repositories.mavenLocal { content { includeGroup(group) } }
            }
            val url = project.providers.gradleProperty("panzer.$id.maven")
                .orElse(project.providers.gradleProperty("${id}MavenUrl")).orNull
                ?: table.entries["maven"]?.takeIf { it.isNotBlank() }
            if (url != null) {
                project.repositories.maven {
                    name = "${id.replaceFirstChar { it.uppercase() }}Maven"
                    setUrl(url)
                    content { includeGroup(group) }
                }
            }
            project.dependencies.add(table.entries["configuration"] ?: "implementation", "$coordinates:$version")
        }
    }

    /**
     * `collectNatives`: copies `natives/<os>/<arch>/<file>` of every
     * `[natives.<name>]` platform into the jar as `natives/<os>-<arch>/<file>`
     * (`jar_name_<os>` renames it there).
     *
     * `-Ppanzer.native.mode=fat|auto|off` (every platform, host only, none),
     * `-Ppanzer.native.target=<os>-<arch>` (one platform) and
     * `-Ppanzer.native.strict=true` (CI: every declared platform of every
     * library must be present). `-P<mod id>.native.*` works too. A missing
     * `required` library only warns, except for the host platform under
     * `buildAndCollect`, which can always be built locally.
     */
    fun natives(project: Project, props: ModBuildProperties) {
        val specs = props.natives.values
        if (specs.isEmpty()) return

        fun flag(name: String): String? = project.providers.gradleProperty("panzer.native.$name")
            .orElse(project.providers.gradleProperty("${props.modId}.native.$name")).orNull
        val collecting = project.gradle.startParameter.taskNames.any { it.endsWith("buildAndCollect") }
        val mode = if (collecting) "fat" else flag("mode") ?: "fat"
        val target = flag("target")?.takeIf { it.isNotBlank() }?.let(::normalizeTarget)
        val strict = flag("strict").toBoolean()
        val host = NativePlatform.host()
        val sourceRoot = project.rootDir.resolve("natives")
        val outDir = project.layout.buildDirectory.dir("generated/natives")

        fun selected(platform: NativePlatform): Boolean = when {
            target != null -> platform.classifier == target
            mode == "off" -> false
            mode == "auto" -> platform == host
            else -> true
        }

        val collect = project.tasks.register("collectNatives", Copy::class.java) {
            group = "panzer"
            description = "Stages natives/<os>/<arch>/ into the jar as natives/<os>-<arch>/."
            // Every selected file is declared whether or not it exists yet: the host's
            // may be built by buildNative<Name> in this same run (-Ppanzer.native.build),
            // and a missing source is simply skipped by the copy. Which ones are
            // missing is therefore decided when the task runs, not when it is configured.
            val expected = mutableListOf<ExpectedNative>()
            for (spec in specs) {
                for (platform in spec.platforms.filter(::selected)) {
                    val file = sourceRoot.resolve(platform.sourcePath).resolve(spec.fileName(platform))
                    from(file) {
                        into("natives/${platform.classifier}")
                        spec.jarName(platform)?.let { name -> rename { name } }
                    }
                    val hostRequired = spec.required && collecting && platform == host
                    expected += ExpectedNative(file, "${spec.name} for ${platform.classifier} (natives/${platform.sourcePath}/${spec.fileName(platform)})",
                        strict || hostRequired, strict || spec.required)
                }
            }
            into(outDir)
            doFirst {
                val missing = expected.filter { it.report && !it.file.exists() }
                if (missing.isNotEmpty()) {
                    val message = "[panzer] Missing native libraries: ${missing.joinToString { it.label }}. Build them " +
                            "(buildNatives, or CI) first; without them those platforms use the mod's Java fallback."
                    if (expected.any { it.fatal && !it.file.exists() }) throw GradleException(message) else logger.warn(message)
                }
            }
        }

        project.extensions.getByType(SourceSetContainer::class.java).named("main") {
            resources.srcDir(outDir)
        }
        project.tasks.named("processResources") { dependsOn(collect) }
    }

    /** A native library collectNatives stages, checked for when the task runs. */
    private data class ExpectedNative(val file: java.io.File, val label: String, val fatal: Boolean, val report: Boolean) :
        java.io.Serializable

    /** Accepts `linux-x86_64`, `linux_x86_64` and Rust-style triples (`x86_64-pc-windows-msvc`). */
    private fun normalizeTarget(raw: String): String = when {
        raw.contains("pc-windows") -> if (raw.startsWith("aarch64")) "windows-aarch64" else "windows-x86_64"
        raw.contains("apple-darwin") -> if (raw.startsWith("aarch64")) "macos-aarch64" else "macos-x86_64"
        raw.contains("linux") && raw.contains("-unknown-") -> "linux-${raw.substringBefore('-')}"
        else -> raw.replace('_', '-').replace("x86-64", "x86_64")
    }
}
