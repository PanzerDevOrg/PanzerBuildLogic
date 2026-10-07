@file:Suppress("UnstableApiUsage")

import com.panzer.gradle.ModBuildProperties
import com.panzer.gradle.PanzerModExtension
import dev.kikugie.stonecutter.build.StonecutterBuildExtension
import net.fabricmc.loom.api.LoomGradleExtensionAPI

// A Fabric mod node (`<version>-fabric`), built with Fabric Loom against
// Mojang's names: the remapping Loom (net.fabricmc.fabric-loom-remap) for the
// obfuscated 1.21.x game, which turns the jar into intermediary names in
// remapJar, and the plain one (net.fabricmc.fabric-loom) for unobfuscated
// 26.x. Everything loader-independent is panzer.mod-common.

plugins {
    java
}

val stonecutter = extensions.getByType(StonecutterBuildExtension::class.java)
val unobfuscated = stonecutter.current.parsed >= "26.1"
apply(plugin = if (unobfuscated) "net.fabricmc.fabric-loom" else "net.fabricmc.fabric-loom-remap")
apply(plugin = "panzer.mod-common")

val modProps: ModBuildProperties = extensions.getByType(PanzerModExtension::class.java).props
val loom = extensions.getByType(LoomGradleExtensionAPI::class.java)

// 1.21.x: dependencies Loom must remap go through mod* configurations; 26.x
// has nothing to remap.
val modConfiguration = if (unobfuscated) "implementation" else "modImplementation"

dependencies {
    add("minecraft", "com.mojang:minecraft:${modProps.mcVersion}")
    if (!unobfuscated) {
        add("mappings", loom.officialMojangMappings())
    }
    add(modConfiguration, "net.fabricmc:fabric-loader:${modProps.fabricLoaderVersion}")
    add(modConfiguration, "net.fabricmc.fabric-api:fabric-api:${modProps.fabricApiVersion}")
}

// Development runs get the JVM flags the [jvm.modules.*] code needs. Fabric
// loads mods on the class path (the unnamed module), so --enable-native-access
// =ALL-UNNAMED does cover them here, unlike under NeoForge.
afterEvaluate {
    val args = modProps.jvmModules.values.filter { it.addToRuntime }.flatMap { it.jvmRunArgs() }.distinct()
    loom.runs.configureEach {
        args.forEach { vmArg(it) }
        runDir("run")
    }
}

// Stonecutter has to write this node's sources before Loom reads them.
tasks.matching { it.name == "processResources" || it.name == "compileJava" }.configureEach {
    dependsOn("stonecutterGenerate")
}
