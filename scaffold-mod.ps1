# Requires -Version 5.1
[CmdletBinding()]
param (
    [Parameter(Mandatory=$false, Position=0)]
    [string]$Destination,

    [Parameter(Mandatory=$false, Position=1)]
    [string]$ModId,

    [Parameter(Mandatory=$false, Position=2)]
    [string]$ModGroup = "com.example.mods"
)

if (-not $Destination -or -not $ModId) {
    Write-Host "Usage: .\scaffold-mod.ps1 <destination-path> <mod_id> [mod_group]"
    Write-Host "Example: .\scaffold-mod.ps1 ..\new-mod new_mod com.example.mods"
    exit 1
}

if (Test-Path -Path $Destination) {
    Write-Error "Error: '$Destination' already exists. This script only scaffolds new projects."
    exit 1
}

$BuildLogicDir = $PSScriptRoot

$PackagePath = $ModGroup.Replace('.', '\')
$JavaDirPath = Join-Path -Path $Destination -ChildPath "src\main\java\$PackagePath\$ModId"
$ResourcesDirPath = Join-Path -Path $Destination -ChildPath "src\main\resources"

New-Item -ItemType Directory -Force -Path $JavaDirPath | Out-Null
New-Item -ItemType Directory -Force -Path $ResourcesDirPath | Out-Null

Copy-Item -Path (Join-Path $BuildLogicDir "templates\settings.gradle.kts") -Destination (Join-Path $Destination "settings.gradle.kts")

$TomlContent = @"
[mod]
id = "$ModId"
version = "0.1.0"
group = "$ModGroup"

[stonecutter]
# "legacy" is defined in panzer-build-logic/common.stonecutter.properties.toml.
# Swap to an explicit "versions = [...]" array, or add "extra_versions"/
# "exclude_versions" here, if this mod needs a different set.
profile = "legacy"
vcs_version = "1.21.1"
"@

Set-Content -Path (Join-Path $Destination "mod.stonecutter.properties.toml") -Value $TomlContent -Encoding UTF8

$GradleBuildContent = @"
plugins {
    id("panzer.neoforge-mod")
}
"@

Set-Content -Path (Join-Path $Destination "build.gradle.kts") -Value $GradleBuildContent -Encoding UTF8

$GitIgnoreContent = @'
.gradle/
build/
*.iml
.idea/
stonecutter.properties.toml
'@

Set-Content -Path (Join-Path $Destination ".gitignore") -Value $GitIgnoreContent -Encoding UTF8

Copy-Item -Path (Join-Path $BuildLogicDir "templates\gradle.properties") -Destination (Join-Path $Destination "gradle.properties")

Write-Host "'$ModId' scaffold created in '$Destination'."
Write-Host ""
Write-Host "Remaining steps (not automatable from here):"
Write-Host "  1. cd '$Destination' && '$BuildLogicDir\sync-mod.ps1' ."
Write-Host "  2. If '$Destination' sits next to panzer-build-logic as a sibling folder, that's used"
Write-Host "     directly (no build/publish step needed). Otherwise the plugin is pulled from"
Write-Host "     panzer-build-logic's published Maven repo at the version in gradle.properties"
Write-Host "     ('panzerBuildLogicVersion') -- see README.md, 'Two ways a mod can consume this repo'."
Write-Host "  3. Review mod.stonecutter.properties.toml -- active versions and mod-specific tables"
