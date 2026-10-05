@file:Suppress("DestructuringDeclaration")

import com.panzer.gradle.CMakeBuildTask
import com.panzer.gradle.JvmModulePreprocessor
import com.panzer.gradle.ModBuildProperties
import com.panzer.gradle.ModPackaging
import com.panzer.gradle.NativePlatform
import com.panzer.gradle.NeoForgeMutexPlugin
import com.panzer.gradle.OptimizeTexturesTask
import com.panzer.gradle.PanzerModExtension
import com.panzer.gradle.PlatformJars
import com.panzer.gradle.PreprocessJvmModuleTask
import com.panzer.gradle.TomlBlockReader
import dev.kikugie.stonecutter.build.StonecutterBuildExtension
import java.util.*
import java.util.zip.ZipFile

plugins {
    id("net.neoforged.moddev")
    java
}

apply(mapOf("plugin" to NeoForgeMutexPlugin::class.java))

val hasStonecutterExtension =
    project.extensions.findByType(StonecutterBuildExtension::class.java) != null

val modProps = if (hasStonecutterExtension) {
    ModBuildProperties.from(project)
} else {
    val tomlFile = rootProject.file("stonecutter.properties.toml")
    val activeVersion = tomlFile.readLines()
        .map { it.trim() }
        .firstOrNull { it.startsWith("vcs_version") }
        ?.substringAfter("=")
        ?.trim()
        ?.removeSurrounding("\"")
        ?.removeSurrounding("'")
        ?: error("Could not read 'vcs_version' from '${tomlFile.path}' to resolve :example's active sibling node.")

    project.evaluationDependsOn(":$activeVersion")
    project(":$activeVersion").extensions.getByType(PanzerModExtension::class.java).props
}

extensions.create("panzerMod", PanzerModExtension::class.java, modProps)

version = "${modProps.modVersion}+${modProps.currentVersion}"
base.archivesName.set(modProps.modId)
group = modProps.modGroup

repositories {
    mavenCentral()
    maven("https://api.modrinth.com/maven") {
        name = "Modrinth"
        content { includeGroup("maven.modrinth") }
    }
    maven("https://maven.neoforged.net/releases/") {
        name = "NeoForged"
    }
}

java {
    withSourcesJar()
    targetCompatibility = modProps.requiredJava
    sourceCompatibility = modProps.requiredJava

    toolchain {
        vendor.set(JvmVendorSpec.AZUL)
        languageVersion.set(JavaLanguageVersion.of(modProps.requiredJava.majorVersion))
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

val mainSourceSet = sourceSets.main.get()
val moduleSourceSets = mutableMapOf<String, SourceSet>()

for ((name, module) in modProps.jvmModules) {
    val javaMajor = modProps.requiredJava.majorVersion
    val baseSrcDir = rootDir.resolve("src/$name/java")

    if (!baseSrcDir.exists()) {
        logger.warn(
            "[panzer.neoforge-mod] [jvm.modules.$name] is declared in stonecutter.properties.toml " +
                    "but 'src/$name/java' does not exist for $path. Create it (even empty) to silence " +
                    "this warning, or remove the table if unused."
        )
        continue
    }

    // Preprocessed once per distinct javaMajor under the ROOT build dir (not
    // this subproject's), so every Stonecutter version subproject that
    // resolves to the same JDK (e.g. every 1.21.x on Java 21) shares one
    // task instance and one output directory -- Gradle's up-to-date check
    // then skips re-running this for the 2nd..Nth subproject on that JDK.
    val preprocessTaskName = "preprocessJvmModule${name.replaceFirstChar { it.uppercase() }}Java$javaMajor"
    val generatedDir = rootProject.layout.buildDirectory.dir("generated/panzerJvmModules/java$javaMajor/$name")
    val generatedDirFile = generatedDir.get().asFile

    // Also run the preprocessor synchronously right now, during Gradle's
    // configuration phase -- not only later as a task action. IDEs (IntelliJ's
    // Gradle sync in particular) build their "this folder has real, indexable
    // .java files with completion" model from what's on disk on the
    // filesystem *at sync time*, before any compile task ever runs. Without
    // this eager pass, `generatedDir` is empty (or missing) right after a
    // fresh clone/clean, so the IDE has nothing to index and shows the
    // sourceSet as a plain "Java" folder instead of proper sources with
    // autocomplete. This eager run is cheap (a handful of small .java files,
    // pure text processing, no compilation) and idempotent; the real
    // `PreprocessJvmModuleTask` below still re-runs it on every build via
    // normal Gradle up-to-date checking, so behaviour at build time is
    // unchanged -- this is purely to make the IDE's initial snapshot correct.
    if (!rootProject.tasks.names.contains(preprocessTaskName)) {
        JvmModulePreprocessor.processDirectory(baseSrcDir, generatedDirFile, javaMajor.toInt())
    }

    val preprocessTask = rootProject.tasks.run {
        if (names.contains(preprocessTaskName)) {
            named<PreprocessJvmModuleTask>(preprocessTaskName)
        } else {
            register<PreprocessJvmModuleTask>(preprocessTaskName) {
                group = "panzer"
                description = "Preprocesses src/$name/java for Java $javaMajor (//? blocks)."
                sourceDir.set(baseSrcDir)
                this.javaMajor.set(javaMajor.toInt())
                outputDir.set(generatedDir)
            }
        }
    }

    val moduleSourceSet = sourceSets.create(name) {
        java.setSrcDirs(listOf(generatedDir))
        val resourcesDir = project.file("src/$name/resources")
        if (resourcesDir.exists()) {
            resources.setSrcDirs(listOf(resourcesDir))
        }
    }
    tasks.named(moduleSourceSet.compileJavaTaskName) {
        dependsOn(preprocessTask)
    }

    moduleSourceSets[name] = moduleSourceSet

    val implementationConfig = "${name}Implementation"
    val compileOnlyConfig = "${name}CompileOnly"
    val runtimeOnlyConfig = "${name}RuntimeOnly"
    val compileClasspathConfig = "${name}CompileClasspath"
    val runtimeClasspathConfig = "${name}RuntimeClasspath"

    configurations {
        named(implementationConfig) {
            extendsFrom(configurations["implementation"], configurations["compileOnly"])
        }
        named(runtimeOnlyConfig) {
            extendsFrom(configurations["runtimeOnly"])
        }
        named(compileClasspathConfig) {
            extendsFrom(configurations["compileClasspath"])
        }
        named(runtimeClasspathConfig) {
            extendsFrom(configurations["runtimeClasspath"])
        }
    }

    dependencies {
        add(compileOnlyConfig, mainSourceSet.output)
    }

    tasks.named<JavaCompile>(module.compileTaskName) {
        dependsOn("compileJava")
        options.compilerArgs.addAll(
            buildList {
                addAll(module.javaCompilerArgs())
                if (module.enablePreview) add("-Xlint:-preview")
            }
        )
    }

    dependencies {
        testImplementation(moduleSourceSet.output)
        testRuntimeOnly(moduleSourceSet.output)
    }

    tasks.named<Jar>("jar") {
        from(moduleSourceSet.output)
    }
}

tasks.named("compileTestJava", JavaCompile::class) {
    options.compilerArgs.add("-Xlint:deprecation")

    if (modProps.anyModuleNeedsPreview()) {
        options.compilerArgs.add("--enable-preview")
    }

    val testAddModules = modProps.jvmModules.values
        .mapNotNull { it.addModules.takeIf { s -> s.isNotBlank() } }
        .distinct()
        .joinToString(",")
    if (testAddModules.isNotBlank()) {
        options.compilerArgs.add("--add-modules=$testAddModules")
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
                        moduleSourceSets[module.name]?.let { sourceSet(it) }
                    }
                }
            }
        }
    }
}

tasks.withType<Test>().configureEach {
    useJUnitPlatform()
    val argsList = mutableListOf<String>().apply {
        if (modProps.anyModuleNeedsPreview()) add("--enable-preview")
        for (module in modProps.jvmModules.values) {
            if (module.addModules.isNotBlank()) add("--add-modules=${module.addModules}")
        }
    }
    jvmArgs(argsList.distinct())
}

dependencies {
    testImplementation(platform("org.junit:junit-bom:${modProps.junitVersion}"))

    testImplementation("org.junit.jupiter:junit-jupiter-api")
    testRuntimeOnly("org.junit.jupiter:junit-jupiter-engine")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

// Packaging every mod shares (see ModPackaging): [depends_on.*] dependencies,
// neoforge.mods.toml / mixin expansion, legal files in every jar, natives,
// buildAndCollect and the per-system jars of [publish] platforms.
if (hasStonecutterExtension) {
    val mergedTables = TomlBlockReader.parse(rootProject.file("stonecutter.properties.toml"))
    ModPackaging.dependencies(project, modProps, mergedTables)
    ModPackaging.resources(project, modProps, mergedTables)
    ModPackaging.natives(project, modProps)
    ModPackaging.jars(project, modProps)
    PlatformJars.register(project, modProps.publishPlatforms, modProps.modVersion)
}

if (hasStonecutterExtension) {
    tasks.named("compileJava") {
        dependsOn("stonecutterGenerate")
    }
    tasks.named("compileTestJava") {
        dependsOn("stonecutterGenerateTest")
    }
    tasks.named("createMinecraftArtifacts") {
        dependsOn("stonecutterGenerate")
    }
}

configurations {
    named("testCompileClasspath") { extendsFrom(configurations["compileClasspath"]) }
    named("testRuntimeClasspath") { extendsFrom(configurations["runtimeClasspath"]) }
}

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
                        .filter { it.name.startsWith("META-INF/") && it.name.endsWith("-runtime-requirements.properties") }
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
    val ownArgs = runtimeModules.flatMap { it.jvmRunArgs() }
    val propagatedArgsProvider = collectPropagatedJvmArgs(project)

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

val resourceDirs = mutableListOf<File>()

for ((name, _) in modProps.jvmModules) {
    val moduleResDir = project.file("src/$name/resources")
    if (moduleResDir.exists()) {
        resourceDirs.add(moduleResDir)
    }
}

val optimizeTasks = resourceDirs.mapIndexed { index, dir ->
    val taskName = "optimizeTextures${if (index == 0) "" else "_$index"}"
    if (tasks.findByName(taskName) == null) {
        tasks.register<OptimizeTexturesTask>(taskName) {
            group = "panzer"
            description = "Optimize PNGs in ${dir.relativeTo(projectDir)} using oxipng."
            resourcesDir.set(dir)
            rootCacheDir.set(layout.buildDirectory.dir("panzer-tools-cache"))
            tools.set(modProps.tools)
            // Lets Gradle mark this task UP-TO-DATE when the PNGs haven't changed
            // since the last successful run -- see the property's doc for why.
            optimizedMarker.set(layout.buildDirectory.file("panzer-tools-cache/$taskName.receipt"))
        }
    } else {
        tasks.named(taskName)
    }
}

if (tasks.findByName("optimizeAllTextures") == null) {
    tasks.register("optimizeAllTextures") {
        group = "panzer"
        description = "Optimize all textures (main and modules)."
        dependsOn(optimizeTasks)
    }
}

if (tasks.findByName("optimizeTextures") == null) {
    tasks.register("optimizeTextures") {
        group = "panzer"
        description = "Optimize all textures (main and modules)."
        dependsOn("optimizeAllTextures")
    }
}

if (tasks.findByName("optimizeAllTextures") != null) {
    tasks.named("processResources") {
        dependsOn("optimizeAllTextures")
    }
}

// ---------------------------------------------------------------------
// Native libraries ([natives.<name>] in the TOML)
//
// Registered once on the root project (natives do not vary per Minecraft
// version), like the JVM-module preprocess tasks above:
//   buildNative<Name>   CMake build for the host -> natives/<os>/<arch>/<file>
//   buildNatives        all of the above
//   nativeHeaders<Name> zip of the public headers, for a `native-headers`
//                       publication artifact
// -Ppanzer.native.build=true makes processResources depend on buildNatives,
// so the jar carries a freshly built host binary (CI does this); without it
// the committed binaries under natives/ are used as they are.
// ---------------------------------------------------------------------

val nativeSpecs = modProps.natives.values
if (nativeSpecs.isNotEmpty()) {
    val host = NativePlatform.host()
    val buildTasks = nativeSpecs.filter { it.cmakeDir != null }.map { spec ->
        val taskName = "buildNative${spec.taskSuffix}"
        if (rootProject.tasks.names.contains(taskName)) {
            rootProject.tasks.named(taskName)
        } else {
            rootProject.tasks.register<CMakeBuildTask>(taskName) {
                group = "panzer"
                description = "Builds native library '${spec.name}' (${spec.cmakeDir}) for ${host.classifier} with CMake."
                sourceDir.set(rootProject.layout.projectDirectory.dir(spec.cmakeDir!!))
                target.set(spec.name)
                buildType.set(providers.gradleProperty("panzer.native.buildType").orElse("Release"))
                cmakeArgs.set(providers.gradleProperty("panzer.native.cmakeArgs")
                    .map { it.split(' ').filter(String::isNotBlank) }.orElse(emptyList()))
                runTests.set(providers.gradleProperty("panzer.native.test").map { it.toBoolean() }.orElse(true))
                buildDir.set(rootProject.layout.buildDirectory.dir("native/${spec.name}/${host.classifier}"))
                outputLibrary.set(rootProject.layout.projectDirectory.file("natives/${host.sourcePath}/${spec.fileName(host)}"))
            }
        }
    }

    for (spec in nativeSpecs.filter { it.headersDir != null }) {
        val taskName = "nativeHeaders${spec.taskSuffix}"
        if (!rootProject.tasks.names.contains(taskName)) {
            rootProject.tasks.register<Zip>(taskName) {
                group = "panzer"
                description = "Packages the public headers of native library '${spec.name}'."
                from(rootProject.layout.projectDirectory.dir(spec.headersDir!!))
                archiveBaseName.set("${modProps.modId}-${spec.name}")
                archiveVersion.set(modProps.modVersion)
                archiveClassifier.set("native-headers")
                destinationDirectory.set(rootProject.layout.buildDirectory.dir("native/headers"))
            }
        }
    }

    if (!rootProject.tasks.names.contains("buildNatives")) {
        rootProject.tasks.register("buildNatives") {
            group = "panzer"
            description = "Builds every CMake-backed [natives.*] library for the host platform."
            dependsOn(buildTasks)
        }
    }

    if (providers.gradleProperty("panzer.native.build").map { it.toBoolean() }.getOrElse(false)) {
        tasks.named("processResources") {
            dependsOn(buildTasks)
        }
        tasks.matching { it.name == "collectNatives" }.configureEach {
            dependsOn(buildTasks)
        }
    }
}
