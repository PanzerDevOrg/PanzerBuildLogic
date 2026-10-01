@file:Suppress("unused")

package com.panzer.gradle

import org.gradle.process.ExecOperations
import java.io.File
import java.net.URI
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap
import java.util.zip.ZipFile

/**
 * Resolves an external tool's binary (oxipng, and anything added later) against a
 * local cache, downloading it if needed. Adding a new tool means implementing
 * [ToolAssetLocator]; the cache/download/extract/fallback cycle is shared.
 *
 * Falling back to a cached binary (even when the download fails, e.g. no network)
 * is intentional: a possibly-outdated binary is preferable to breaking the build.
 */
fun interface ToolAssetLocator {
    /** Given (target-triple, file extension), builds the download URL. Null if there is no asset for that platform. */
    fun assetUrl(target: String, extension: String, version: String): String?
}

class ExternalToolResolver(
    private val execOperations: ExecOperations,
    private val rootCacheDir: File,
    private val logPrefix: String,
) {
    companion object {
        /** One lock object per absolute cache-file path, shared across every resolver instance in this JVM/daemon. */
        private val CACHE_LOCKS = ConcurrentHashMap<String, Any>()
    }

    /**
     * Resolves (or downloads) the binary named [exeBaseName] for [spec], using
     * [locator] to build the URL. Returns null if the platform isn't supported,
     * the tool is disabled, or the download fails with no prior cache to fall back on.
     *
     * Locking is keyed per target file, not per [ExternalToolResolver] instance --
     * callers (e.g. [OptimizeTexturesTask]) construct a fresh resolver per task
     * execution, so an instance-level lock would never actually serialize anything.
     * A key on the resolved cache path means unrelated tools/versions still resolve
     * in parallel (important with `org.gradle.parallel=true` across subprojects),
     * while two tasks racing to populate the exact same cache entry don't corrupt it.
     */
    fun resolve(spec: ToolSpec, exeBaseName: String, locator: ToolAssetLocator): File? {
        if (!spec.enabled) {
            println("[$logPrefix] ${spec.name} disabled (tools.${spec.name}.enabled=false). Skipping.")
            return null
        }

        val version = spec.requireVersion()
        val osName = System.getProperty("os.name").lowercase(Locale.ROOT)
        val osArch = System.getProperty("os.arch").lowercase(Locale.ROOT)

        val (target, extension) = platformTarget(osName, osArch) ?: run {
            println("[$logPrefix] Unsupported platform for ${spec.name} ($osName/$osArch). Skipping.")
            return null
        }

        val exeName = if (osName.contains("win")) "$exeBaseName.exe" else exeBaseName
        val cacheDir = File(rootCacheDir, "${spec.name}-cache/$version")
        val targetExe = File(cacheDir, exeName)

        if (targetExe.exists() && targetExe.length() > 0L) return targetExe

        val lock = CACHE_LOCKS.computeIfAbsent(targetExe.absolutePath) { Any() }
        return synchronized(lock) { downloadAndExtract(spec, version, target, extension, exeName, cacheDir, targetExe, osName, locator) }
    }

    private fun downloadAndExtract(
        spec: ToolSpec,
        version: String,
        target: String,
        extension: String,
        exeName: String,
        cacheDir: File,
        targetExe: File,
        osName: String,
        locator: ToolAssetLocator,
    ): File? {
        // Re-check inside the lock: another thread may have finished the download
        // while this one was waiting to acquire it.
        if (targetExe.exists() && targetExe.length() > 0L) return targetExe

        cacheDir.mkdirs()

        val downloadUrl = locator.assetUrl(target, extension, version) ?: run {
            println("[$logPrefix] Could not build the download URL for ${spec.name} v$version ($target).")
            return null
        }

        val tempArchive = File(cacheDir, "download-${System.currentTimeMillis()}.$extension")

        return try {
            println("[$logPrefix] Downloading ${spec.name} v$version ($target)...")

            URI.create(downloadUrl).toURL().openStream().use { input ->
                Files.copy(input, tempArchive.toPath(), StandardCopyOption.REPLACE_EXISTING)
            }

            when (extension) {
                "zip" -> extractFromZip(tempArchive, exeName, targetExe, osName)
                else -> extractFromTarGz(tempArchive, cacheDir, targetExe, osName)
            }

            tempArchive.delete()
            targetExe
        } catch (e: Exception) {
            tempArchive.delete()
            if (targetExe.exists() && targetExe.length() > 0L) {
                println("[$logPrefix] Download of ${spec.name} failed (${e.message}); using cached binary.")
                return targetExe
            }
            println("[$logPrefix] Failed downloading/extracting ${spec.name}: ${e.message}")
            null
        }
    }

    private fun extractFromZip(archive: File, exeName: String, targetExe: File, osName: String) {
        ZipFile(archive).use { zip ->
            val entry = zip.entries().asSequence().firstOrNull { it.name.endsWith(exeName) }
                ?: throw IllegalStateException("$exeName binary not found inside ZIP archive")

            zip.getInputStream(entry).use { inStream ->
                val tempExe = File(targetExe.parentFile, "$exeName.tmp")
                Files.copy(inStream, tempExe.toPath(), StandardCopyOption.REPLACE_EXISTING)

                if (!osName.contains("win")) tempExe.setExecutable(true)

                Files.move(
                    tempExe.toPath(), targetExe.toPath(),
                    StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE,
                )
            }
        }
    }

    private fun extractFromTarGz(archive: File, cacheDir: File, targetExe: File, osName: String) {
        execOperations.exec {
            commandLine("tar", "-xzf", archive.absolutePath, "-C", cacheDir.absolutePath)
        }
        if (!osName.contains("win")) targetExe.setExecutable(true)
    }

    private fun platformTarget(osName: String, osArch: String): Pair<String, String>? = when {
        osName.contains("win") -> {
            val arch = if (osArch.contains("64")) "x86_64" else "i686"
            "$arch-pc-windows-msvc" to "zip"
        }
        osName.contains("mac") -> {
            val arch = if (osArch.contains("aarch64") || osArch.contains("arm")) "aarch64" else "x86_64"
            "$arch-apple-darwin" to "tar.gz"
        }
        osName.contains("nux") || osName.contains("nix") -> {
            val arch = if (osArch.contains("aarch64") || osArch.contains("arm")) "aarch64" else "x86_64"
            "$arch-unknown-linux-musl" to "tar.gz"
        }
        else -> null
    }
}
