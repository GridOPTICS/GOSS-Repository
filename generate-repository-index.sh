#!/bin/bash
#
# Generate Repository Index for GOSS Archive (Shell Wrapper)
#
# This script is a backward-compatible wrapper around generate_repository_index.py
# For full functionality, use the Python script directly.
#
# Usage: ./generate-repository-index.sh [--force] [repository-path]
#
# Examples:
#   ./generate-repository-index.sh                  # Process dependencies (default)
#   ./generate-repository-index.sh release          # Process release repository
#   ./generate-repository-index.sh --force all      # Force regenerate all
#   ./generate-repository-index.sh dependencies     # Process dependencies
#

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PYTHON_SCRIPT="$SCRIPT_DIR/generate_repository_index.py"

# Check if Python script exists
if [ ! -f "$PYTHON_SCRIPT" ]; then
    echo "Error: Python script not found: $PYTHON_SCRIPT"
    exit 1
fi

# Check for Python 3
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is required but not installed"
    exit 1
fi

# Parse arguments and convert to Python script format
ARGS=()
FORCE=false
REPO=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --force)
            FORCE=true
            ARGS+=("--force")
            shift
            ;;
        --help|-h)
            exec python3 "$PYTHON_SCRIPT" --help
            ;;
        *)
            REPO="$1"
            shift
            ;;
    esac
done

# Map legacy repository paths to new names
case "$REPO" in
    ""|"dependencies")
        ARGS+=("dependencies")
        ;;
    "release"|"snapshot"|"all")
        ARGS+=("$REPO")
        ;;
    "cnf/releaserepo")
        # Legacy path - skip if directory doesn't exist
        if [ -d "$SCRIPT_DIR/cnf/releaserepo" ]; then
            echo "Warning: cnf/releaserepo is deprecated. Processing as custom path."
            # For custom paths, fall back to processing all JARs in that directory
            cd "$SCRIPT_DIR/$REPO" && python3 "$PYTHON_SCRIPT" --force .
            exit $?
        else
            echo "Directory not found: $REPO (skipping)"
            exit 0
        fi
        ;;
    *)
        # Custom path - check if it exists
        if [ -d "$SCRIPT_DIR/$REPO" ] || [ -d "$REPO" ]; then
            ARGS+=("$REPO")
        else
            echo "Error: Unknown repository or path: $REPO"
            echo "Available: dependencies, release, snapshot, all"
            exit 1
        fi
        ;;
esac

# Run Python script
exec python3 "$PYTHON_SCRIPT" "${ARGS[@]}"
