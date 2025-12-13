#!/bin/bash
#
# Generate Repository Index for GOSS Archive
# Creates OSGi R5 repository index and Maven metadata
# Only processes new/modified JAR files for efficiency
#
# Usage: ./generate-repository-index.sh [--force] [repository-path]
# --force: Force regeneration of all metadata (ignore cache)
# Default repository-path: dependencies

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
FORCE_REGEN=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --force)
            FORCE_REGEN=true
            shift
            ;;
        *)
            REPO_DIR="$1"
            shift
            ;;
    esac
done

REPO_DIR="${REPO_DIR:-dependencies}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_debug() {
    if [ "$DEBUG" = "true" ]; then
        echo -e "${BLUE}[DEBUG]${NC} $1"
    fi
}

# Check if repository directory exists
if [ ! -d "$REPO_DIR" ]; then
    log_error "Repository directory not found: $REPO_DIR"
    exit 1
fi

cd "$REPO_DIR"
REPO_DIR="$(pwd)"

log_info "Generating repository index for: $REPO_DIR"

# Check if we're in a git repository
IN_GIT_REPO=false
if git rev-parse --git-dir > /dev/null 2>&1; then
    IN_GIT_REPO=true
    log_info "Git repository detected - will use git to detect changes"
else
    log_warn "Not in a git repository - will use modification times"
fi

# Get list of new/modified JAR files
get_changed_jars() {
    if [ "$FORCE_REGEN" = "true" ]; then
        log_info "Force regeneration - processing all JARs" >&2
        find . -name "*.jar" -type f
        return
    fi

    local changed_jars=()

    if [ "$IN_GIT_REPO" = "true" ]; then
        # Use git to find new/modified files
        # This includes untracked, modified, and added files
        while IFS= read -r file; do
            if [[ "$file" == *.jar ]]; then
                changed_jars+=("$file")
            fi
        done < <(git status --porcelain | grep -E '^\?\?|^ M|^M |^A ' | awk '{print $2}')

        log_info "Found ${#changed_jars[@]} new/modified JAR(s) via git" >&2
    else
        # Fall back to checking modification time against index.xml
        if [ -f "index.xml" ]; then
            local index_mtime=$(stat -c%Y "index.xml" 2>/dev/null || stat -f%m "index.xml" 2>/dev/null)
            while IFS= read -r jar_file; do
                local jar_mtime=$(stat -c%Y "$jar_file" 2>/dev/null || stat -f%m "$jar_file" 2>/dev/null)
                if [ "$jar_mtime" -gt "$index_mtime" ]; then
                    changed_jars+=("$jar_file")
                fi
            done < <(find . -name "*.jar" -type f)
            log_info "Found ${#changed_jars[@]} JAR(s) newer than index.xml" >&2
        else
            # No index exists, process all JARs
            log_warn "No existing index.xml found - processing all JARs" >&2
            find . -name "*.jar" -type f
            return
        fi
    fi

    # Output changed JARs (only if there are any)
    if [ ${#changed_jars[@]} -gt 0 ]; then
        printf '%s\n' "${changed_jars[@]}"
    fi
}

# Extract OSGi metadata from JAR manifest
extract_bundle_metadata() {
    local jar_file="$1"

    # Try unzip first
    local manifest=$(unzip -p "$jar_file" META-INF/MANIFEST.MF 2>/dev/null || true)

    # Fall back to jar command if unzip fails
    if [ -z "$manifest" ]; then
        if command -v jar &> /dev/null; then
            local temp_dir=$(mktemp -d)
            (cd "$temp_dir" && jar -xf "$jar_file" META-INF/MANIFEST.MF 2>/dev/null || true)
            if [ -f "$temp_dir/META-INF/MANIFEST.MF" ]; then
                manifest=$(cat "$temp_dir/META-INF/MANIFEST.MF")
            fi
            rm -rf "$temp_dir"
        fi
    fi

    if [ -z "$manifest" ]; then
        return 1
    fi

    # Extract key OSGi headers
    # Note: Only get the single line for each header, don't use -A flag which pulls extra lines
    local bsn=$(echo "$manifest" | grep "^Bundle-SymbolicName:" | head -1 | sed 's/Bundle-SymbolicName: *//' | sed 's/;.*//' | tr -d '\n\r ')
    local version=$(echo "$manifest" | grep "^Bundle-Version:" | head -1 | sed 's/Bundle-Version: *//' | tr -d '\n\r ')
    local name=$(echo "$manifest" | grep "^Bundle-Name:" | head -1 | sed 's/Bundle-Name: *//' | tr -d '\n\r')

    if [ -z "$bsn" ] || [ -z "$version" ]; then
        return 1
    fi

    echo "$bsn|$version|$name"
}

# Parse existing index.xml to extract bundle entries
parse_existing_index() {
    local index_file="index.xml"

    if [ ! -f "$index_file" ]; then
        return
    fi

    log_info "Parsing existing index.xml..."

    # Extract all resource blocks from existing index
    # This preserves the full XML for bundles we're not updating
    awk '
        /<resource>/ { in_resource=1; resource="" }
        in_resource { resource = resource $0 "\n" }
        /<\/resource>/ {
            in_resource=0
            # Extract the JAR path from url attribute
            if (resource ~ /attribute name="url" value="/) {
                url_line = resource
                sub(/.*attribute name="url" value="/, "", url_line)
                sub(/".*/, "", url_line)
                print url_line "|" resource
            }
        }
    ' "$index_file" > /tmp/existing_bundles_$$.txt

    log_debug "Extracted $(wc -l < /tmp/existing_bundles_$$.txt) existing bundle entries"
}

# Check if maven-metadata.xml is valid and up-to-date for a directory
is_maven_metadata_valid() {
    local artifact_dir="$1"
    local metadata_file="$artifact_dir/maven-metadata.xml"

    # Check if metadata file exists
    if [ ! -f "$metadata_file" ]; then
        log_debug "  No maven-metadata.xml found in $artifact_dir"
        return 1
    fi

    # Get all JAR versions in directory
    local jar_versions=()
    local artifact_name=$(basename "$artifact_dir")
    for jar in "$artifact_dir"/*.jar; do
        if [ -f "$jar" ]; then
            local jar_name=$(basename "$jar")
            local version=$(echo "$jar_name" | sed "s/${artifact_name}-//" | sed 's/.jar$//')
            jar_versions+=("$version")
        fi
    done

    if [ ${#jar_versions[@]} -eq 0 ]; then
        return 1
    fi

    # Check each version is listed in metadata
    for version in "${jar_versions[@]}"; do
        if ! grep -q "<version>$version</version>" "$metadata_file" 2>/dev/null; then
            log_debug "  Version $version missing from maven-metadata.xml"
            return 1
        fi
    done

    # Count versions in metadata vs actual JARs
    local metadata_version_count=$(grep -c "<version>" "$metadata_file" 2>/dev/null || echo "0")
    if [ "$metadata_version_count" -ne "${#jar_versions[@]}" ]; then
        log_debug "  Version count mismatch: metadata=$metadata_version_count, jars=${#jar_versions[@]}"
        return 1
    fi

    log_debug "  maven-metadata.xml is valid in $artifact_dir"
    return 0
}

# Generate Maven metadata for an artifact directory
generate_maven_metadata() {
    local artifact_dir="$1"
    local artifact_name=$(basename "$artifact_dir")

    # Extract groupId and artifactId from artifact name
    local group_id=$(echo "$artifact_name" | sed 's/\.[^.]*$//' | sed 's/\./\//g')
    local artifact_id=$(echo "$artifact_name" | sed 's/.*\.//')

    # Find all versions
    local versions=()
    local latest_version=""
    local last_updated=$(date -u +"%Y%m%d%H%M%S")

    for jar in "$artifact_dir"/*.jar; do
        if [ -f "$jar" ]; then
            local jar_name=$(basename "$jar")
            local version=$(echo "$jar_name" | sed "s/${artifact_name}-//" | sed 's/.jar$//')
            versions+=("$version")
        fi
    done

    # Sort versions and get latest
    if [ ${#versions[@]} -gt 0 ]; then
        latest_version=$(printf '%s\n' "${versions[@]}" | sort -V | tail -1)
    else
        return 1
    fi

    # Generate maven-metadata.xml
    local metadata_file="$artifact_dir/maven-metadata.xml"

    cat > "$metadata_file" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<metadata>
  <groupId>$(echo "$group_id" | sed 's/\//./g')</groupId>
  <artifactId>$artifact_id</artifactId>
  <versioning>
    <latest>$latest_version</latest>
    <release>$latest_version</release>
    <versions>
EOF

    for version in $(printf '%s\n' "${versions[@]}" | sort -V); do
        echo "      <version>$version</version>" >> "$metadata_file"
    done

    cat >> "$metadata_file" << EOF
    </versions>
    <lastUpdated>$last_updated</lastUpdated>
  </versioning>
</metadata>
EOF

    log_debug "  Generated Maven metadata: $metadata_file"
}

# Escape special XML characters in attribute values
xml_escape() {
    local str="$1"
    # Must escape & first, then other characters
    str="${str//&/&amp;}"
    str="${str//</&lt;}"
    str="${str//>/&gt;}"
    str="${str//\"/&quot;}"
    echo "$str"
}

# Generate XML for a bundle resource
generate_bundle_xml() {
    local jar_file="$1"
    local bsn="$2"
    local version="$3"
    local name="$4"

    # Escape XML special characters in values
    bsn=$(xml_escape "$bsn")
    version=$(xml_escape "$version")
    name=$(xml_escape "$name")

    local jar_path="${jar_file#./}"
    local jar_size=$(stat -c%s "$jar_file" 2>/dev/null || stat -f%z "$jar_file" 2>/dev/null)
    local sha256=$(sha256sum "$jar_file" 2>/dev/null | cut -d' ' -f1 || echo "")

    cat << EOF
  <resource>
    <capability namespace="osgi.identity">
      <attribute name="osgi.identity" value="$bsn"/>
      <attribute name="type" value="osgi.bundle"/>
      <attribute name="version" type="Version" value="$version"/>
    </capability>
    <capability namespace="osgi.content">
      <attribute name="osgi.content" value="$sha256"/>
      <attribute name="url" value="$jar_path"/>
      <attribute name="size" type="Long" value="$jar_size"/>
      <attribute name="mime" value="application/vnd.osgi.bundle"/>
    </capability>
EOF

    if [ -n "$name" ]; then
        cat << EOF
    <capability namespace="osgi.bundle">
      <attribute name="osgi.bundle" value="$bsn"/>
      <attribute name="bundle-version" type="Version" value="$version"/>
    </capability>
EOF
    fi

    echo "  </resource>"
}

# Generate OSGi R5 repository index
generate_osgi_index() {
    local index_file="index.xml"
    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")

    log_info "Generating OSGi repository index..."

    # Get list of changed JARs
    local changed_jars_file="/tmp/changed_jars_$$.txt"
    get_changed_jars > "$changed_jars_file"
    local num_changed=$(wc -l < "$changed_jars_file")

    if [ "$num_changed" -eq 0 ] && [ -f "$index_file" ]; then
        log_info "No changes detected - index is up to date"
        rm -f "$changed_jars_file"
        return
    fi

    # Parse existing index
    parse_existing_index

    # Create associative array of changed JAR paths for quick lookup
    local -A changed_jar_map
    while IFS= read -r jar; do
        local jar_path="${jar#./}"
        # Sanitize path for array key (remove any problematic characters)
        local clean_path="${jar_path//[^a-zA-Z0-9._\/-]/}"
        if [ -n "$clean_path" ]; then
            changed_jar_map["$clean_path"]=1
        fi
    done < "$changed_jars_file"

    # Start XML
    cat > "$index_file" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<repository increment="$(date +%s)000" name="GOSS Archive Repository" xmlns="http://www.osgi.org/xmlns/repository/v1.0.0">
EOF

    local bundle_count=0
    local updated_count=0
    local preserved_count=0

    # First, add entries from existing index that haven't changed
    if [ -f "/tmp/existing_bundles_$$.txt" ]; then
        while IFS='|' read -r url resource_xml; do
            # Sanitize URL for array lookup (remove any problematic characters)
            local clean_url="${url//[^a-zA-Z0-9._\/-]/}"
            if [ -n "$url" ] && [ -z "${changed_jar_map[$clean_url]:-}" ]; then
                # This bundle hasn't changed, preserve its entry
                echo "$resource_xml" >> "$index_file"
                preserved_count=$((preserved_count + 1))
                bundle_count=$((bundle_count + 1))
            fi
        done < /tmp/existing_bundles_$$.txt
        rm -f /tmp/existing_bundles_$$.txt
    fi

    log_info "  Preserved $preserved_count existing bundle(s)"

    # Now process changed/new JARs
    while IFS= read -r jar_file; do
        if [ ! -f "$jar_file" ]; then
            continue
        fi

        local metadata
        if metadata=$(extract_bundle_metadata "$jar_file"); then
            IFS='|' read -r bsn version name <<< "$metadata"
            generate_bundle_xml "$jar_file" "$bsn" "$version" "$name" >> "$index_file"
            updated_count=$((updated_count + 1))
            bundle_count=$((bundle_count + 1))
            log_info "  Updated: $jar_file ($bsn $version)"
        else
            log_warn "  Skipping (no OSGi metadata): $jar_file"
        fi
    done < "$changed_jars_file"

    # Close XML
    echo "</repository>" >> "$index_file"

    log_info "  Total bundles in index: $bundle_count"
    log_info "  New/Updated: $updated_count"
    log_info "  Preserved: $preserved_count"

    # Generate compressed version
    log_info "Generating compressed index (index.xml.gz)..."
    gzip -c "$index_file" > "${index_file}.gz"

    log_info "${GREEN}✓ OSGi index generated: $index_file${NC}"
    log_info "${GREEN}✓ Compressed index: ${index_file}.gz${NC}"

    # Cleanup
    rm -f "$changed_jars_file"
}

# Update Maven metadata only for directories with changed JARs or invalid metadata
update_maven_metadata() {
    log_info "Updating Maven metadata..."

    local artifact_count=0
    local skipped_count=0
    local dirs_to_update=()

    # Get changed JAR files
    local changed_jars_file="/tmp/changed_jars_maven_$$.txt"
    get_changed_jars > "$changed_jars_file"

    # Extract unique directories containing changed JARs
    local updated_dirs=()
    while IFS= read -r jar_file; do
        local dir=$(dirname "$jar_file")
        updated_dirs+=("$dir")
    done < "$changed_jars_file"

    # Remove duplicates and sort
    local unique_dirs=($(printf '%s\n' "${updated_dirs[@]}" | sort -u))

    # For directories with changed JARs, check if metadata needs updating
    for artifact_dir in "${unique_dirs[@]}"; do
        if [ -d "$artifact_dir" ] && ls "$artifact_dir"/*.jar 1> /dev/null 2>&1; then
            if ! is_maven_metadata_valid "$artifact_dir"; then
                dirs_to_update+=("$artifact_dir")
            else
                skipped_count=$((skipped_count + 1))
                log_debug "  Skipping (metadata valid): $artifact_dir"
            fi
        fi
    done

    # If force regen, also check all other directories for missing/invalid metadata
    if [ "$FORCE_REGEN" = "true" ]; then
        # Get all unique directories containing JARs
        local all_jar_dirs=($(find . -name "*.jar" -type f -exec dirname {} \; | sort -u))
        for dir in "${all_jar_dirs[@]}"; do
            if [ -d "$dir" ] && ! is_maven_metadata_valid "$dir"; then
                dirs_to_update+=("$dir")
            fi
        done
        # Deduplicate
        dirs_to_update=($(printf '%s\n' "${dirs_to_update[@]}" | sort -u))
    fi

    if [ ${#dirs_to_update[@]} -eq 0 ]; then
        log_info "No Maven metadata updates needed"
        rm -f "$changed_jars_file"
        return
    fi

    # Generate metadata for directories that need it
    for artifact_dir in "${dirs_to_update[@]}"; do
        generate_maven_metadata "$artifact_dir"
        artifact_count=$((artifact_count + 1))
        log_info "  Updated Maven metadata: $artifact_dir"
    done

    log_info "Updated Maven metadata for $artifact_count artifact(s), skipped $skipped_count (already valid)"
    rm -f "$changed_jars_file"
}

# Main execution
echo ""
log_info "========================================"
log_info "GOSS Repository Index Generator"
log_info "========================================"
echo ""

# Update Maven metadata
update_maven_metadata

# Generate OSGi repository index
generate_osgi_index

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Repository Index Update Complete!${NC}"
echo -e "  Repository:       ${BLUE}$REPO_DIR${NC}"
echo -e "  OSGi index:       ${GREEN}index.xml${NC}"
echo -e "  Compressed index: ${GREEN}index.xml.gz${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

log_info "${GREEN}✓ Done!${NC}"
