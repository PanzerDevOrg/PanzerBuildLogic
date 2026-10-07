@file:Suppress("DestructuringDeclaration")

import com.panzer.gradle.ModBuildProperties
import com.panzer.gradle.NeoForgeMutexPlugin
import com.panzer.gradle.PanzerModExtension
import dev.kikugie.stonecutter.build.StonecutterBuildExtension
import java.util.*
import java.util.zip.ZipFile

// A NeoForge mod node (ModDevGradle). The loader-independent part of the
// build is panzer.mod-common.

plugins {
    id("net.neoforged.moddev")
    java
    id("panzer.mod-common")
}

apply(mapOf("plugin" to NeoForgeMutexPlugin::class.java))

val hasStonecutterExtension =
    project.extensions.findByType(StonecutterBuildExtension::class.java) != null
val modProps: ModBuildProperties = extensions.getByType(PanzerModExtension::class.java).props
val mainSourceSet = sourceSets.main.get()

repositories {
    maven("https://maven.neoforged.net/releases/") {
        name = "NeoForged"
    }
}

neoForge {
    version = modProps.neoVersion

    val parchmentVersion = modProps.parchmentVersion.isNotBlank()

    if (parchmentVersion) {
        parchment {
            mappingsVersion = modProps.parchmentVersion
            minecraftVersion = modProps.mcVersion
        }
    }
}

// This must happen before the NeoForge extension is finalised,
// so we do it in the configuration phase, not in afterEvaluate.
neoForge {
    mods {
        if (hasStonecutterExtension) {
            register(modProps.modId) {
                sourceSet(mainSourceSet)
                for (module in modProps.jvmModules.values) {
                    if (module.addToRuntime) {
                        sourceSets.findByName(module.name)?.let { sourceSet(it) }
                    }
                }
            }
        }
    }
}

if (hasStonecutterExtension) {
    tasks.named("createMinecraftArtifacts") {
        dependsOn("stonecutterGenerate")
    }
}

/**
 * A dependency's runtime-requirements file: under META-INF/, or at the jar root
 * where Celeris 0.2.0 - 0.2.2 put it.
 */
fun isRuntimeRequirements(entryName: String): Boolean =
    entryName.endsWith("-runtime-requirements.properties") &&
            (entryName.startsWith("META-INF/") || '/' !in entryName)

/**
 * Drops --enable-native-access from NeoForge run arguments. FML loads every mod
 * as a named module in its own module layer. On Java 21, once the flag is on
 * the command line, restricted FFM calls from any module it does not list throw
 * IllegalCallerException, and no value can list a mod (the JVM only matches
 * boot-layer modules), so it switched Celeris's native physics and zstd off.
 * On Java 22+ it only covers class-path code, which a mod is not.
 */
fun withoutNativeAccess(args: List<String>): List<String> =
    args.filterNot { it.startsWith("--enable-native-access") }

fun collectPropagatedJvmArgs(project: Project): Provider<List<String>> {
    val runtimeClasspath = project.configurations.findByName("runtimeClasspath")
        ?: return project.provider { emptyList() }

    return project.provider {
        val seen = linkedSetOf<String>()
        val files = runCatching {
            runtimeClasspath.incoming.artifactView { lenient(true) }.files.files
        }.getOrElse { emptySet() }

        for (file in files) {
            if (!file.name.endsWith(".jar")) continue
            runCatching {
                ZipFile(file).use { zip ->
                    zip.entries().asSequence()
                        .filter { isRuntimeRequirements(it.name) }
                        .forEach { entry ->
                            val props = Properties().apply { zip.getInputStream(entry).use { load(it) } }
                            props.getProperty("jvm.args")
                                ?.split(Regex("\\s+"))
                                ?.filter { it.isNotBlank() }
                                ?.forEach { seen.add(it) }
                        }
                }
            }.onFailure {
                project.logger.warn("[panzer.neoforge-mod] Failed reading runtime-requirements metadata from ${file.name}: ${it.message}")
            }
        }
        seen.toList()
    }
}

afterEvaluate {
    val runtimeModules = modProps.jvmModules.values.filter { it.addToRuntime }
    val ownArgs = withoutNativeAccess(runtimeModules.flatMap { it.jvmRunArgs() })
    val propagatedArgsProvider = collectPropagatedJvmArgs(project).map(::withoutNativeAccess)

    neoForge {
        runs {
            all {
                for (arg in ownArgs) {
                    jvmArgument(arg)
                }
                jvmArguments.addAll(propagatedArgsProvider)
            }
        }
    }
}
