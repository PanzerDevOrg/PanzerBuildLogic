@file:Suppress("unused")

package com.panzer.gradle

import org.gradle.api.Project
import javax.inject.Inject

open class PanzerModExtension @Inject constructor(
    val props: ModBuildProperties,
) {
    fun req(project: Project, key: String): String = props.req(project, key)
    fun optional(project: Project, key: String, default: String = ""): String = props.optional(project, key, default)
    fun tool(name: String): ToolSpec = props.tools.get(name)
}
