package com.panzer.gradle

import org.gradle.api.Plugin
import org.gradle.api.Project
import org.gradle.api.services.BuildService
import org.gradle.api.services.BuildServiceParameters

interface NeoForgeMutex : BuildService<BuildServiceParameters.None>

class NeoForgeMutexPlugin : Plugin<Project> {
    override fun apply(project: Project) {
        val mutex = project.gradle.sharedServices.registerIfAbsent(
            "createMinecraftArtifactsMutex",
            NeoForgeMutex::class.java,
        ) {
            maxParallelUsages.set(1)
        }

        project.tasks.named { it == "createMinecraftArtifacts" }.configureEach {
            usesService(mutex)
        }
    }
}
