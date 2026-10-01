#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: ./scaffold-mod.sh <destination-path> <mod_id> [mod_group]"
  echo "Example: ./scaffold-mod.sh ../new-mod new_mod com.example.mods"
  exit 1
fi

DEST="$1"
MOD_ID="$2"
MOD_GROUP="${3:-com.example.mods}"

if [ -e "$DEST" ]; then
  echo "Error: '$DEST' already exists. This script only scaffolds new projects."
  exit 1
fi

BUILD_LOGIC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$DEST"
mkdir -p "$DEST/src/main/java/${MOD_GROUP//./\/}/${MOD_ID}"
mkdir -p "$DEST/src/main/resources"

cp "$BUILD_LOGIC_DIR/templates/settings.gradle.kts" "$DEST/settings.gradle.kts"

cat > "$DEST/mod.stonecutter.properties.toml" << EOF
[mod]
id = "$MOD_ID"
version = "0.1.0"
group = "$MOD_GROUP"

[stonecutter]
# "legacy" is defined in panzer-build-logic/common.stonecutter.properties.toml.
# Swap to an explicit "versions = [...]" array, or add "extra_versions"/
# "exclude_versions" here, if this mod needs a different set.
profile = "legacy"
vcs_version = "1.21.1"
EOF

cat > "$DEST/build.gradle.kts" << EOF
plugins {
    id("panzer.neoforge-mod")
}
EOF

cat > "$DEST/.gitignore" << 'EOF'
.gradle/
build/
*.iml
.idea/
stonecutter.properties.toml
EOF

cp "$BUILD_LOGIC_DIR/templates/gradle.properties" "$DEST/gradle.properties"

echo "'$MOD_ID' scaffold created in '$DEST'."
echo ""
echo "Remaining steps (not automatable from here):"
echo "  1. cd '$DEST' && '$BUILD_LOGIC_DIR/sync-mod.sh' . -- copies gradlew/gradle-wrapper.jar and settings.gradle.kts/gradle.properties"
echo "  2. If '$DEST' sits next to panzer-build-logic as a sibling folder, that's used"
echo "     directly (no build/publish step needed). Otherwise the plugin is pulled from"
echo "     panzer-build-logic's published Maven repo at the version in gradle.properties"
echo "     ('panzerBuildLogicVersion') -- see README.md, 'Two ways a mod can consume this repo'."
echo "  3. Review mod.stonecutter.properties.toml -- active versions and mod-specific tables"
