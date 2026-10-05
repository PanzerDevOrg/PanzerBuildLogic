import com.panzer.gradle.PanzerModExtension

// Shared build logic: panzer-build-logic's panzer.neoforge-mod (dependencies,
// resources, legal files, natives, buildAndCollect). Only what is specific to
// {{name}} belongs here.
plugins {
    id("panzer.neoforge-mod")
    idea
}

val modProps = extensions.getByType(PanzerModExtension::class.java).props
val mainSourceSet = sourceSets.main.get()

neoForge {
    runs {
        all {
            val runDir = rootProject.file("versions/${modProps.currentVersion}/run")
            if (!runDir.exists()) runDir.mkdirs()

            sourceSet = mainSourceSet
            gameDirectory = runDir
        }

        register("client") {
            client()
        }

        register("server") {
            server()
            programArgument("--nogui")
        }
    }
}
