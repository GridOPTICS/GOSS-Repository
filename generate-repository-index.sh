#!/bin/bash
#
# Generate Repository Index for GOSS Archive
# Creates OSGi R5 repository index and Maven metadata
#
# Usage: ./generate-repository-index.sh [repository-path]
# Default: cnf/releaserepo

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_DIR="${1:-cnf/releaserepo}"

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

# Check if repository directory exists
if [ ! -d "$REPO_DIR" ]; then
    log_error "Repository directory not found: $REPO_DIR"
    exit 1
fi

cd "$REPO_DIR"

log_info "Generating repository index for: $(pwd)"

# Extract OSGi metadata from JAR manifest
extract_bundle_metadata() {
    local jar_file="$1"
    local manifest=$(unzip -p "$jar_file" META-INF/MANIFEST.MF 2>/dev/null || echo "")

    if [ -z "$manifest" ]; then
        return 1
    fi

    # Extract key OSGi headers (handle line continuations)
    local bsn=$(echo "$manifest" | grep -A5 "^Bundle-SymbolicName:" | tr -d '\n\r' | sed 's/Bundle-SymbolicName: *//' | sed 's/;.*//' | tr -d ' ')
    local version=$(echo "$manifest" | grep "^Bundle-Version:" | sed 's/Bundle-Version: *//' | tr -d '\n\r ')
    local name=$(echo "$manifest" | grep "^Bundle-Name:" | sed 's/Bundle-Name: *//' | tr -d '\n\r')

    if [ -z "$bsn" ] || [ -z "$version" ]; then
        return 1
    fi

    echo "$bsn|$version|$name"
}

# Generate Maven metadata for an artifact directory
generate_maven_metadata() {
    local artifact_dir="$1"
    local artifact_name=$(basename "$artifact_dir")

    # Extract groupId and artifactId from artifact name
    # e.g., pnnl.goss.core.goss-client -> groupId=pnnl.goss.core, artifactId=goss-client
    local group_id=$(echo "$artifact_name" | sed 's/\.[^.]*$//' | sed 's/\./\//g')
    local artifact_id=$(echo "$artifact_name" | sed 's/.*\.//')

    # Find all versions
    local versions=()
    local latest_version=""
    local last_updated=$(date -u +"%Y%m%d%H%M%S")

    for jar in "$artifact_dir"/*.jar; do
        if [ -f "$jar" ]; then
            local jar_name=$(basename "$jar")
            # Extract version from filename: artifact-version.jar
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

    # Add all versions (sorted)
    for version in $(printf '%s\n' "${versions[@]}" | sort -V); do
        echo "      <version>$version</version>" >> "$metadata_file"
    done

    cat >> "$metadata_file" << EOF
    </versions>
    <lastUpdated>$last_updated</lastUpdated>
  </versioning>
</metadata>
EOF

    log_info "  Generated Maven metadata: $artifact_dir/maven-metadata.xml"
}

# Generate OSGi R5 repository index
generate_osgi_index() {
    local index_file="index.xml"
    local timestamp=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")

    log_info "Generating OSGi repository index..."

    # Start XML
    cat > "$index_file" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<repository increment="$(date +%s)000" name="GOSS Archive Repository" xmlns="http://www.osgi.org/xmlns/repository/v1.0.0">
EOF

    local bundle_count=0

    # Process all JARs
    while IFS= read -r jar_file; do
        if [ ! -f "$jar_file" ]; then
            continue
        fi

        local metadata=$(extract_bundle_metadata "$jar_file")
        if [ $? -eq 0 ]; then
            IFS='|' read -r bsn version name <<< "$metadata"
            local jar_path="${jar_file#./}"
            local jar_size=$(stat -f%z "$jar_file" 2>/dev/null || stat -c%s "$jar_file")

            # Add resource entry
            cat >> "$index_file" << EOF
  <resource>
    <capability namespace="osgi.identity">
      <attribute name="osgi.identity" value="$bsn"/>
      <attribute name="type" value="osgi.bundle"/>
      <attribute name="version" type="Version" value="$version"/>
    </capability>
    <capability namespace="osgi.content">
      <attribute name="osgi.content" value="$(sha256sum "$jar_file" | cut -d' ' -f1)"/>
      <attribute name="url" value="$jar_path"/>
      <attribute name="size" type="Long" value="$jar_size"/>
      <attribute name="mime" value="application/vnd.osgi.bundle"/>
    </capability>
EOF

            # Add bundle capability if we have a name
            if [ -n "$name" ]; then
                cat >> "$index_file" << EOF
    <capability namespace="osgi.bundle">
      <attribute name="osgi.bundle" value="$bsn"/>
      <attribute name="bundle-version" type="Version" value="$version"/>
    </capability>
EOF
            fi

            echo "  </resource>" >> "$index_file"
            bundle_count=$((bundle_count + 1))
        fi
    done < <(find . -name "*.jar" -type f)

    # Close XML
    echo "</repository>" >> "$index_file"

    log_info "  Added $bundle_count bundles to index"

    # Generate compressed version
    log_info "Generating compressed index (index.xml.gz)..."
    gzip -c "$index_file" > "${index_file}.gz"

    log_info "${GREEN}✓ OSGi index generated: $index_file${NC}"
    log_info "${GREEN}✓ Compressed index: ${index_file}.gz${NC}"
}

# Main execution
log_info "Scanning repository for artifacts..."

artifact_count=0

# Generate Maven metadata for each artifact directory
for artifact_dir in */; do
    if [ -d "$artifact_dir" ]; then
        # Check if directory contains JARs
        if ls "$artifact_dir"/*.jar 1> /dev/null 2>&1; then
            generate_maven_metadata "$artifact_dir"
            artifact_count=$((artifact_count + 1))
        fi
    fi
done

log_info "Generated Maven metadata for $artifact_count artifacts"

# Generate OSGi repository index
generate_osgi_index

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Repository Index Generation Complete!${NC}"
echo -e "  Artifacts:        $artifact_count"
echo -e "  Maven metadata:   ${GREEN}maven-metadata.xml${NC} in each artifact directory"
echo -e "  OSGi index:       ${GREEN}index.xml${NC}"
echo -e "  Compressed index: ${GREEN}index.xml.gz${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

log_info "${GREEN}✓ Done!${NC}"
