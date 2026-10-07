@file:Suppress("unused")

package com.panzer.gradle

import dev.kikugie.stonecutter.build.StonecutterBuildExtension
import org.gradle.api.JavaVersion
import org.gradle.api.Project

/**
 * Optional JVM module declared under `[jvm.modules.<name>]` (e.g. an FFM module
 * with `--enable-preview`). [rawProperties] holds every resolved key from that
 * table so a mod can add custom flags without touching Kotlin here.
 */
data class OptionalJvmModule(
    val name: String,
    val enablePreview: Boolean,
    val addModules: String,
    /**
     * Target passed to `--enable-native-access=<value>` (e.g. "ALL-UNNAMED", or a
     * comma-separated module list). Empty means the flag is omitted entirely.
     * Not a boolean: `--enable-native-access` always takes a target argument, it's
     * never a bare on/off switch, so the TOML value itself carries that target
     * rather than gating a hardcoded "ALL-UNNAMED".
     */
    val enableNativeAccess: String,
    val addToRuntime: Boolean,
    val rawProperties: Map<String, String> = emptyMap(),
) {
    val sourceSetName: String get() = name
    val compileTaskName: String get() = "compile${name.replaceFirstChar { it.uppercase() }}Java"

    fun javaCompilerArgs(): List<String> = buildList {
        if (enablePreview) add("--enable-preview")
        if (addModules.isNotBlank()) add("--add-modules=$addModules")
    }

    fun jvmRunArgs(): List<String> = buildList {
        if (enablePreview) add("--enable-preview")
        if (addModules.isNotBlank()) add("--add-modules=$addModules")
        if (enableNativeAccess.isNotBlank()) add("--enable-native-access=$enableNativeAccess")
    }

    /** Reads a custom key declared under this table (e.g. "custom_flag" -> "jvm.modules.$name.custom_flag"). */
    fun prop(key: String, default: String = ""): String =
        rawProperties["jvm.modules.$name.$key"] ?: default
}

data class ModBuildProperties(
    val modId: String,
    val modVersion: String,
    val modGroup: String,
    /**
     * The Stonecutter node being built: the Minecraft version for NeoForge
     * ("1.21.1"), `<version>-fabric` for Fabric. Used wherever builds must not
     * collide: jar versions, run directories, `{mc}` in dependency artifacts.
     */
    val currentVersion: String,
    val requiredJava: JavaVersion,
    val neoVersion: String,
    val mcVersion: String,
    val parchmentVersion: String,
    val junitVersion: String,
    val tools: ToolVersions,
    /** Optional JVM modules declared under [jvm.modules.*], indexed by name. */
    val jvmModules: Map<String, OptionalJvmModule>,
    /** Native libraries declared under [natives.*], indexed by name. */
    val natives: Map<String, NativeLibrarySpec> = emptyMap(),
    /** Per-system jars from `[publish] platforms` (see [PlatformJars]). */
    val publishPlatforms: List<String> = emptyList(),
    /** "neoforge" or "fabric" (nodes named `<version>-fabric`). */
    val loader: String = "neoforge",
    /** Fabric only: `[fabric] loader_version` and the version's `fabric_api_version`. */
    val fabricLoaderVersion: String = "",
    val fabricApiVersion: String = "",
) {
    val isFabric: Boolean get() = loader == "fabric"

    companion object {
        /**
         * Gradle-property prefix that lets a single build turn an optional JVM
         * module on or off without editing the TOML:
         *
         * ```
         * ./gradlew runClient -Ppanzer.jvmModule.ffm.enabled=false
         * ```
         *
         * Useful to force a mod into compatibility mode (or the reverse: turn on a
         * module the TOML has disabled) for one run, e.g. to test the fallback
         * path without switching JDKs. When not passed, `add_to_runtime` from
         * `stonecutter.properties.toml` is used as-is (no behavior change).
         */
        private const val RUNTIME_OVERRIDE_PREFIX = "panzer.jvmModule."
        private const val RUNTIME_OVERRIDE_SUFFIX = ".enabled"

        fun from(project: Project): ModBuildProperties {
            val sc = project.extensions.getByType(StonecutterBuildExtension::class.java)

            fun req(key: String): String =
                freshProperty(project, key) ?: sc.readPropertyAsString(key)
                    ?: error("Property '$key' is missing in 'stonecutter.properties.toml' for active target '${sc.current.version}'.")

            fun optional(key: String, default: String = ""): String =
                freshProperty(project, key) ?: sc.readPropertyAsString(key) ?: default

            val parsed = sc.current.parsed
            val requiredJava = when {
                parsed >= "26.1" -> JavaVersion.VERSION_25
                parsed >= "1.20.5" -> JavaVersion.VERSION_21
                parsed >= "1.18" -> JavaVersion.VERSION_17
                parsed >= "1.17" -> JavaVersion.VERSION_16
                else -> error("Unsupported Minecraft target version '${sc.current.version}' for Java toolchain selection.")
            }

            // Parsed exactly once per project and threaded through everything below
            // (module discovery, tool versions, version overrides) instead of each
            // of those re-reading and re-parsing the same file off disk on its own --
            // this runs once per Stonecutter version subproject on every single
            // Gradle configuration pass, so a redundant file read here is a redundant
            // read times every subproject times every build.
            val mergedToml = project.rootProject.file("stonecutter.properties.toml")
            val tables = TomlBlockReader.parse(mergedToml)
            val moduleNames = discoverModuleNames(tables)
            val allProperties = collectAllProperties(tables, moduleNames)

            fun runtimeOverride(name: String): Boolean? {
                val raw = project.findProperty("$RUNTIME_OVERRIDE_PREFIX$name$RUNTIME_OVERRIDE_SUFFIX") as? String
                    ?: return null
                val parsedFlag = raw.toBooleanStrictOrNull()
                if (parsedFlag == null) {
                    project.logger.warn(
                        "[panzer.jvmModule] '$RUNTIME_OVERRIDE_PREFIX$name$RUNTIME_OVERRIDE_SUFFIX=$raw' is not " +
                                "'true'/'false' -- ignoring, falling back to 'add_to_runtime' from the TOML."
                    )
                    return null
                }
                return parsedFlag
            }

            val jvmModules = moduleNames.associateWith { name ->
                val declaredAddToRuntime = optional("jvm.modules.$name.add_to_runtime", "true").toBoolean()
                val effectiveAddToRuntime = runtimeOverride(name) ?: declaredAddToRuntime
                if (effectiveAddToRuntime != declaredAddToRuntime) {
                    project.logger.lifecycle(
                        "[panzer.jvmModule] '$name' ${if (effectiveAddToRuntime) "enabled" else "disabled"} " +
                                "via -P$RUNTIME_OVERRIDE_PREFIX$name$RUNTIME_OVERRIDE_SUFFIX " +
                                "(TOML declares add_to_runtime=$declaredAddToRuntime for '${sc.current.version}')."
                    )
                }
                OptionalJvmModule(
                    name = name,
                    enablePreview = optional("jvm.modules.$name.enable_preview", "false").toBoolean(),
                    addModules = optional("jvm.modules.$name.add_modules", ""),
                    enableNativeAccess = resolveNativeAccessTarget(
                        optional("jvm.modules.$name.enable_native_access", "")
                    ),
                    addToRuntime = effectiveAddToRuntime,
                    rawProperties = allProperties,
                )
            }

            val modId = req("mod.id")
            val overrides = VersionOverrides.resolve(tables, modId, sc.current.version)

            fun overrideOrReq(key: String): String =
                overrides[key] ?: req(key)

            fun overrideOrOptional(key: String, default: String = ""): String =
                overrides[key] ?: optional(key, default)

            val fabric = sc.current.project.endsWith("-fabric")
            return ModBuildProperties(
                modId = modId,
                modVersion = req("mod.version"),
                modGroup = req("mod.group"),
                currentVersion = sc.current.project,
                requiredJava = requiredJava,
                neoVersion = overrideOrReq("neo_version"),
                mcVersion = overrideOrReq("minecraft_version"),
                parchmentVersion = overrideOrOptional("parchment_mappings_version"),
                junitVersion = req("dependencies.junit"),
                tools = ToolVersions.from(tables),
                jvmModules = jvmModules,
                natives = NativeLibrarySpec.from(tables),
                publishPlatforms = PlatformJars.parseList(TomlBlockReader.find(tables, "publish")?.entries?.get("platforms")),
                loader = if (fabric) "fabric" else "neoforge",
                fabricLoaderVersion = if (fabric) req("fabric.loader_version") else "",
                fabricApiVersion = if (fabric) overrideOrReq("fabric_api_version") else "",
            )
        }

        /**
         * Resolves [key] from a fresh parse of the merged stonecutter.properties.toml:
         * dotted keys ("mod.version") from their table, plain keys from the active
         * ["<version>"] block with [overrides.<mod_id>."<version>"] applied.
         *
         * Stonecutter's own `properties` reader caches the parsed file for the
         * lifetime of the Gradle daemon, so after editing the TOML it kept serving
         * the previous values (e.g. stale version ranges in the built jar) until
         * the daemon restarted. Reading the file here avoids that.
         */
        internal fun freshProperty(project: Project, key: String): String? {
            val tables = TomlBlockReader.parse(project.rootProject.file("stonecutter.properties.toml"))
            if (tables.isEmpty()) return null
            if (key.contains('.')) {
                val table = TomlBlockReader.find(tables, *key.substringBeforeLast('.').split('.').toTypedArray())
                return table?.entries?.get(key.substringAfterLast('.'))
            }
            val sc = project.extensions.findByType(StonecutterBuildExtension::class.java) ?: return null
            val modId = TomlBlockReader.find(tables, "mod")?.entries?.get("id") ?: return null
            if (TomlBlockReader.find(tables, sc.current.version) == null) return null
            return VersionOverrides.resolve(tables, modId, sc.current.version)[key]
        }

        /** Reads a property from Stonecutter trying String, Long, and Boolean types. */
        private fun StonecutterBuildExtension.readPropertyAsString(key: String): String? {
            properties.getOrNull<String>(key)?.let { return it }
            properties.getOrNull<Long>(key)?.let { return it.toString() }
            properties.getOrNull<Boolean>(key)?.let { return it.toString() }
            return null
        }

        /** Finds [jvm.modules.<name>] table names among already-parsed tables. */
        private fun discoverModuleNames(tables: List<TomlBlockReader.Table>): Set<String> =
            tables.asSequence()
                .filter { it.path.size == 3 && it.path[0] == "jvm" && it.path[1] == "modules" }
                .map { it.path[2] }
                .toSet()

        /** Flattens every [jvm.modules.*] key to "jvm.modules.<name>.<key>" -> value, for OptionalJvmModule.prop(). */
        private fun collectAllProperties(tables: List<TomlBlockReader.Table>, moduleNames: Set<String>): Map<String, String> {
            if (moduleNames.isEmpty()) return emptyMap()
            return buildMap {
                for (name in moduleNames) {
                    val table = TomlBlockReader.find(tables, "jvm", "modules", name) ?: continue
                    for ((key, value) in table.entries) {
                        put("jvm.modules.$name.$key", value)
                    }
                }
            }
        }

        /**
         * Interprets `enable_native_access`: "true"/"false" as a convenience for the
         * common case (maps to "ALL-UNNAMED" / disabled), anything else is used
         * verbatim as the `--enable-native-access=<value>` target -- covers the real
         * TOML shape (`enable_native_access = "ALL-UNNAMED"`) as well as a
         * comma-separated module list, without a silent-false trap for either.
         */
        private fun resolveNativeAccessTarget(raw: String): String = when (raw.trim().lowercase()) {
            "", "false" -> ""
            "true" -> "ALL-UNNAMED"
            else -> raw.trim()
        }
    }

    /** Whether any optional module needs --enable-preview. */
    fun anyModuleNeedsPreview(): Boolean = jvmModules.values.any { it.enablePreview }

    fun module(name: String): OptionalJvmModule =
        jvmModules[name] ?: error(
            "No JVM module named '$name' in stonecutter.properties.toml -- " +
                    "check the table header exists and is spelled correctly."
        )

    /** Reads a mandatory property from Stonecutter for the currently active project. */
    fun req(project: Project, key: String): String {
        val sc = project.extensions.getByType(StonecutterBuildExtension::class.java)
        return freshProperty(project, key)
            ?: sc.properties.getOrNull<String>(key)
            ?: sc.properties.getOrNull<Long>(key)?.toString()
            ?: sc.properties.getOrNull<Boolean>(key)?.toString()
            ?: error("Property '$key' is missing in 'stonecutter.properties.toml' for active target '${sc.current.version}'.")
    }

    /** Reads an optional property from Stonecutter with a default fallback. */
    fun optional(project: Project, key: String, default: String = ""): String {
        val sc = project.extensions.getByType(StonecutterBuildExtension::class.java)
        return freshProperty(project, key)
            ?: sc.properties.getOrNull<String>(key)
            ?: sc.properties.getOrNull<Long>(key)?.toString()
            ?: sc.properties.getOrNull<Boolean>(key)?.toString()
            ?: default
    }
}
