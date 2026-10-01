import com.panzer.gradle.PanzerModExtension
import net.neoforged.moddevgradle.dsl.NeoForgeExtension

plugins {
    id("panzer.neoforge-mod")
}

val modProps = extensions.getByType(PanzerModExtension::class.java).props

version = "${modProps.modVersion}-example+${modProps.currentVersion}"
base.archivesName.set("${modProps.modId}-example")
group = "${modProps.modGroup}.example"

dependencies {
    // NOTE: project(":") resolves to the Stonecutter controller root
    compileOnly(project(":${modProps.currentVersion}"))
    runtimeOnly(project(":${modProps.currentVersion}"))
}

extensions.configure<NeoForgeExtension> {
    mods {
        create(modProps.modId) {
            sourceSet(project(":${modProps.currentVersion}").sourceSets.main.get())
        }
        create("${modProps.modId}_example") {
            sourceSet(sourceSets.main.get())
        }
    }
}
