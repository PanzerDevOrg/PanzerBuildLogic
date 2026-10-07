plugins {
    `kotlin-dsl`
    `maven-publish`
}

// group/version only matter for the published-plugin consumption path (see README
// "Two ways a mod can consume this repo"). When a mod uses the sibling-folder
// includeBuild path instead, Gradle substitutes the plugin directly from source and
// never looks at these coordinates.
group = "org.panzer.panzer"
version = providers.gradleProperty("panzerBuildLogicVersion").getOrElse("0.0.0-local")

repositories {
    gradlePluginPortal()
    mavenCentral()
    maven("https://maven.kikugie.dev/releases") { name = "KikuGie Releases" }
    maven("https://maven.kikugie.dev/snapshots") { name = "KikuGie Snapshots" }
    maven("https://maven.neoforged.net/releases/") { name = "NeoForged" }
    maven("https://maven.fabricmc.net/") { name = "Fabric" }
}

// Reads [plugins] from the common TOML, instead of duplicating the Stonecutter/ModDev
// versions as a string literal here and in the TOML.
fun pluginVersion(key: String): String {
    val toml = File(rootDir, "common.stonecutter.properties.toml")
    val line = toml.readLines()
        .map { it.trim() }
        .dropWhile { it != "[plugins]" }
        .drop(1)
        .takeWhile { !it.startsWith("[") }
        .firstOrNull { it.startsWith("$key ") || it.startsWith("$key=") }
        ?: error("Could not find '$key' under [plugins] in common.stonecutter.properties.toml")
    return line.substringAfter("=").trim().removeSurrounding("\"")
}

dependencies {
    implementation("dev.kikugie.stonecutter:dev.kikugie.stonecutter.gradle.plugin:${pluginVersion("stonecutter")}")
    implementation("net.neoforged.moddev:net.neoforged.moddev.gradle.plugin:${pluginVersion("moddev")}")
    implementation("net.fabricmc:fabric-loom:${pluginVersion("loom")}")
    implementation("org.gradle.toolchains:foojay-resolver:${pluginVersion("foojay")}")
    implementation("com.google.code.gson:gson:2.14.0")
}

// Class-based plugins. panzer.settings is a settings plugin: a mod's
// settings.gradle.kts applies it after includeBuild("../panzer-build-logic"),
// which puts this build (and Stonecutter, ModDevGradle, Foojay) on the settings
// classpath shared by every project of the mod.
gradlePlugin {
    plugins {
        register("panzerSettings") {
            id = "panzer.settings"
            implementationClass = "com.panzer.gradle.PanzerSettingsPlugin"
        }
        register("panzerMod") {
            id = "panzer.mod"
            implementationClass = "com.panzer.gradle.PanzerModPlugin"
        }
        register("panzerStonecutter") {
            id = "panzer.stonecutter"
            implementationClass = "com.panzer.gradle.PanzerStonecutterPlugin"
        }
    }
}

// `kotlin-dsl` auto-registers every `src/main/kotlin/*.gradle.kts` precompiled script
// plugin (panzer.neoforge-mod, panzer.neoforge-mod-example) as a real Gradle plugin,
// markers included -- `maven-publish` just needs a `publishing {}` block to know where
// to put them. Publishing goes to a plain directory, not a real Maven server: CI copies
// that directory's contents onto the `gh-pages` branch (see
// .github/workflows/publish.yml) so consumers hit a static, unauthenticated URL.
publishing {
    repositories {
        maven {
            name = "GitHubPagesStaging"
            url = uri(layout.buildDirectory.dir("publish-repo"))
        }
    }
}
