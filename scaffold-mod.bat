@echo off
setlocal enabledelayedexpansion

set "DEST=%~1"
set "MOD_ID=%~2"
set "MOD_GROUP=%~3"

if "%MOD_GROUP%"=="" set "MOD_GROUP=com.example.mods"

if "%DEST%"=="" (
    goto :usage
)
if "%MOD_ID%"=="" (
    goto :usage
)

if exist "%DEST%" (
    echo Error: '%DEST%' already exists. This script only scaffolds new projects.
    exit /b 1
)

set "BUILD_LOGIC_DIR=%~dp0"
if "%BUILD_LOGIC_DIR:~-1%"=="\" set "BUILD_LOGIC_DIR=%BUILD_LOGIC_DIR:~0,-1%"

set "GROUP_PATH=%MOD_GROUP:.=\%"
set "JAVA_DIR=%DEST%\src\main\java\%GROUP_PATH%\%MOD_ID%"
set "RESOURCES_DIR=%DEST%\src\main\resources"

mkdir "%JAVA_DIR%" 2>nul
mkdir "%RESOURCES_DIR%" 2>nul

copy "%BUILD_LOGIC_DIR%\templates\settings.gradle.kts" "%DEST%\settings.gradle.kts" >nul

(
    echo [mod]
    echo id = "%MOD_ID%"
    echo version = "0.1.0"
    echo group = "%MOD_GROUP%"
    echo.
    echo [stonecutter]
    echo # "legacy" is defined in panzer-build-logic/common.stonecutter.properties.toml.
    echo # Swap to an explicit "versions = [...]" array, or add "extra_versions"/
    echo # "exclude_versions" here, if this mod needs a different set.
    echo profile = "legacy"
    echo vcs_version = "1.21.1"
) > "%DEST%\mod.stonecutter.properties.toml"

(
    echo plugins {
    echo     id("panzer.neoforge-mod"^)
    echo }
) > "%DEST%\build.gradle.kts"

(
    echo .gradle/
    echo build/
    echo *.iml
    echo .idea/
    echo stonecutter.properties.toml
) > "%DEST%\.gitignore"

copy "%BUILD_LOGIC_DIR%\templates\gradle.properties" "%DEST%\gradle.properties" >nul

echo '%MOD_ID%' scaffold created in '%DEST%'.
echo.
echo Remaining steps (not automatable from here^):
echo   1. cd '%DEST%' ^&^& '%BUILD_LOGIC_DIR%\sync-mod.bat' .
echo   2. If '%DEST%' sits next to panzer-build-logic as a sibling folder, that's used
echo      directly (no build/publish step needed^). Otherwise the plugin is pulled from
echo      panzer-build-logic's published Maven repo at the version in gradle.properties
echo      ('panzerBuildLogicVersion'^) -- see README.md, 'Two ways a mod can consume this repo'.
echo   3. Review mod.stonecutter.properties.toml -- active versions and mod-specific tables
exit /b 0

:usage
echo Usage: scaffold-mod.bat ^<destination-path^> ^<mod_id^> [mod_group]
echo Example: scaffold-mod.bat ..\new-mod new_mod com.example.mods
exit /b 1
