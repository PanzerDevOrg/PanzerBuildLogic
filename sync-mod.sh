#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "Usage: ./sync-mod.sh <path-to-mod>"
  echo "Example: ./sync-mod.sh ../mod"
  exit 1
fi

MOD_DIR="$1"
BUILD_LOGIC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -d "$MOD_DIR" ]; then
  echo "Error: '$MOD_DIR' does not exist."
  exit 1
fi

cp "$BUILD_LOGIC_DIR/templates/gradle.properties" "$MOD_DIR/gradle.properties"
echo "gradle.properties synchronized in '$MOD_DIR'."

cp "$BUILD_LOGIC_DIR/templates/settings.gradle.kts" "$MOD_DIR/settings.gradle.kts"
echo "settings.gradle.kts synchronized in '$MOD_DIR'."

if [ ! -f "$BUILD_LOGIC_DIR/gradlew" ]; then
  echo "Warning: '$BUILD_LOGIC_DIR/gradlew' does not exist yet -- cannot regenerate mod wrapper."
  echo "Run 'gradle wrapper' once inside panzer-build-logic/ to generate it, then run this script again."
  exit 0
fi

mkdir -p "$MOD_DIR/gradle/wrapper"
cp "$BUILD_LOGIC_DIR/gradlew" "$MOD_DIR/gradlew"
cp "$BUILD_LOGIC_DIR/gradlew.bat" "$MOD_DIR/gradlew.bat"
cp "$BUILD_LOGIC_DIR/gradle/wrapper/gradle-wrapper.properties" "$MOD_DIR/gradle/wrapper/gradle-wrapper.properties"
if [ -f "$BUILD_LOGIC_DIR/gradle/wrapper/gradle-wrapper.jar" ]; then
  cp "$BUILD_LOGIC_DIR/gradle/wrapper/gradle-wrapper.jar" "$MOD_DIR/gradle/wrapper/gradle-wrapper.jar"
fi
chmod +x "$MOD_DIR/gradlew"

echo "Wrapper regenerated in '$MOD_DIR'."
