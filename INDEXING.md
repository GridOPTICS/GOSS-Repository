# Repository Index Generation

## Overview

The `generate-repository-index.sh` script creates and maintains OSGi R5 repository metadata for all JAR bundles in the GOSS-Repository. The script is optimized to only process new or modified JAR files, making it efficient for incremental updates.

## Usage

### Basic Usage

```bash
cd /home/debian/repos/GridAPPSD/GOSS-Repository
./generate-repository-index.sh
```

This will:
- Detect new/modified JAR files (via git or modification time)
- Extract OSGi metadata from only those JARs
- Preserve existing entries in index.xml
- Generate updated index.xml and index.xml.gz

### Force Full Regeneration

To regenerate metadata for all JARs (useful after corruption or major changes):

```bash
./generate-repository-index.sh --force
```

### Custom Repository Path

By default, the script operates on the `dependencies/` directory. To specify a different path:

```bash
./generate-repository-index.sh /path/to/repository
```

Or with force:

```bash
./generate-repository-index.sh --force /path/to/repository
```

## How It Works

### Change Detection

The script uses two methods to detect changes:

1. **Git Repository** (preferred):
   - Uses `git status --porcelain` to find new, modified, or added JAR files
   - Only processes JARs with status: `??` (untracked), `M` (modified), or `A` (added)

2. **Modification Time** (fallback):
   - If not in a git repository, compares JAR modification times with index.xml
   - Processes JARs newer than the existing index

### Incremental Updates

When updating the index:

1. **Parse Existing Index**: Extracts all `<resource>` blocks from current index.xml
2. **Identify Changed JARs**: Creates a map of JAR paths that have changed
3. **Preserve Unchanged**: Copies resource blocks for unchanged JARs from old index
4. **Process Changed**: Extracts metadata and generates new resource blocks for changed JARs
5. **Merge**: Combines preserved and new entries into updated index.xml

### Maven Metadata

The script also generates `maven-metadata.xml` files for each artifact directory, but only for directories containing changed JARs:

```xml
<metadata>
  <groupId>org.apache.jena.osgi</groupId>
  <artifactId>osgi</artifactId>
  <versioning>
    <latest>5.6.0</latest>
    <release>5.6.0</release>
    <versions>
      <version>4.1.0</version>
      <version>5.6.0</version>
    </versions>
    <lastUpdated>20251121151700</lastUpdated>
  </versioning>
</metadata>
```

## Output Files

### index.xml

OSGi R5 repository index containing metadata for all bundles:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<repository increment="1763766000000" name="GOSS Archive Repository" xmlns="http://www.osgi.org/xmlns/repository/v1.0.0">
  <resource>
    <capability namespace="osgi.identity">
      <attribute name="osgi.identity" value="org.apache.jena.osgi"/>
      <attribute name="type" value="osgi.bundle"/>
      <attribute name="version" type="Version" value="5.6.0"/>
    </capability>
    <capability namespace="osgi.content">
      <attribute name="osgi.content" value="sha256-hash"/>
      <attribute name="url" value="dependencies/org.apache.jena.osgi/org.apache.jena.osgi-5.6.0.jar"/>
      <attribute name="size" type="Long" value="27262976"/>
      <attribute name="mime" value="application/vnd.osgi.bundle"/>
    </capability>
  </resource>
  <!-- More resources... -->
</repository>
```

### index.xml.gz

Compressed version of index.xml for faster downloads.

### maven-metadata.xml

Maven metadata for each artifact directory (e.g., `dependencies/org.apache.jena.osgi/maven-metadata.xml`).

## Workflow Examples

### Adding a New Bundle

```bash
# 1. Copy new bundle to repository
cp /path/to/mybundle-1.0.0.jar dependencies/com.example.mybundle/

# 2. Regenerate index (processes only the new JAR)
cd /home/debian/repos/GridAPPSD/GOSS-Repository
./generate-repository-index.sh

# 3. Commit changes
git add dependencies/com.example.mybundle/mybundle-1.0.0.jar
git add dependencies/com.example.mybundle/maven-metadata.xml
git add index.xml index.xml.gz
git commit -m "Add mybundle 1.0.0"
```

### Updating an Existing Bundle

```bash
# 1. Replace or add new version
cp /path/to/mybundle-2.0.0.jar dependencies/com.example.mybundle/

# 2. Regenerate index
./generate-repository-index.sh

# Script will:
# - Remove old entry for mybundle-1.0.0 (if replaced)
# - Add new entry for mybundle-2.0.0
# - Update maven-metadata.xml with both versions

# 3. Commit
git add dependencies/com.example.mybundle/
git add index.xml index.xml.gz
git commit -m "Update mybundle to 2.0.0"
```

### No Changes Scenario

If you run the script and no JARs have changed:

```bash
$ ./generate-repository-index.sh

[INFO] Generating repository index for: /home/debian/repos/GridAPPSD/GOSS-Repository/dependencies
[INFO] Git repository detected - will use git to detect changes
[INFO] Found 0 new/modified JAR(s) via git
[INFO] Updating Maven metadata...
[INFO] No Maven metadata updates needed
[INFO] Generating OSGi repository index...
[INFO] No changes detected - index is up to date

========================================
Repository Index Update Complete!
  Repository:       /home/debian/repos/GridAPPSD/GOSS-Repository/dependencies
  OSGi index:       index.xml
  Compressed index: index.xml.gz
========================================

[INFO] ✓ Done!
```

## Performance

### Before (Old Script)
- Processed **all** JAR files every time (~500+ JARs in GOSS-Repository)
- Regeneration time: ~2-5 minutes
- Generated completely new index each time

### After (Updated Script)
- Processes **only** new/modified JAR files
- Typical regeneration time: ~5-10 seconds for 1-2 new JARs
- Preserves existing metadata, only updates what changed
- `--force` option available for full regeneration when needed

## Troubleshooting

### "No existing index.xml found"

If this is the first run, the script will process all JARs. This is normal and expected.

### Git Not Detecting Changes

Make sure JAR files are not in `.gitignore`. Check with:

```bash
git check-ignore dependencies/path/to/bundle.jar
```

### Corrupted Index

If the index becomes corrupted, regenerate completely:

```bash
./generate-repository-index.sh --force
```

### Missing unzip or jar Command

The script tries `unzip` first, then falls back to `jar` command. If both are missing:

```bash
# Install unzip (Debian/Ubuntu)
sudo apt-get install unzip

# Or ensure Java is installed (provides jar command)
java -version
```

## Debug Mode

To see detailed debug output:

```bash
DEBUG=true ./generate-repository-index.sh
```

This will show:
- Number of existing bundle entries parsed
- Each JAR being processed
- Maven metadata generation for each directory

## GitHub Actions Automation

The repository includes a GitHub Actions workflow that automatically regenerates indexes when JAR files are pushed to the repository.

### Workflow File

`.github/workflows/regenerate-indexes.yml`

### When It Triggers

The workflow runs automatically when:
- Any `.jar` file is pushed to the `master` branch
- The workflow is manually triggered via GitHub UI (workflow_dispatch)

### What It Does

1. **Checkout Repository**: Fetches the complete repository with full history
2. **Set Up Environment**: Installs required tools (Java 11, unzip, gzip)
3. **Regenerate cnf/releaserepo indexes**: Runs `./generate-repository-index.sh cnf/releaserepo`
4. **Regenerate dependencies indexes**: Runs `./generate-repository-index.sh dependencies`
5. **Check for Changes**: Detects if any index files were modified
6. **Commit and Push**: If changes detected, commits and pushes:
   - `cnf/releaserepo/index.xml`
   - `cnf/releaserepo/index.xml.gz`
   - `cnf/releaserepo/*/maven-metadata.xml`
   - `dependencies/index.xml`
   - `dependencies/index.xml.gz`
   - `dependencies/*/maven-metadata.xml`

### Commit Message Format

When the workflow commits changes:

```
Auto-regenerate repository indexes

Generated by GitHub Actions after JAR file update

[skip ci]
```

The `[skip ci]` tag prevents the workflow from triggering itself recursively.

### Manual Triggering

To manually trigger the workflow:

1. Go to the repository on GitHub
2. Click **Actions** tab
3. Select **Regenerate Repository Indexes** workflow
4. Click **Run workflow** button
5. Select the `master` branch
6. Click **Run workflow**

This is useful when:
- You want to force regeneration without pushing new JARs
- The workflow failed previously and you want to retry
- You've made changes to the script and want to regenerate all indexes

### Viewing Workflow Results

To see workflow execution:

1. Go to **Actions** tab on GitHub
2. Click on the workflow run to see detailed logs
3. Expand each step to see command output
4. Check for any errors or warnings

### Workflow Permissions

The workflow uses `${{ secrets.GITHUB_TOKEN }}` which has permissions to:
- Read repository contents
- Push commits to the repository
- This token is automatically provided by GitHub Actions

### Troubleshooting Workflow Issues

**Workflow doesn't trigger:**
- Verify you pushed to the `master` branch (not `main` or another branch)
- Verify you pushed at least one `.jar` file
- Check workflow file syntax in `.github/workflows/regenerate-indexes.yml`

**Workflow fails during index generation:**
- Check the workflow logs in the Actions tab
- Look for script errors in the "Regenerate indexes" steps
- Verify all JARs have valid OSGi metadata
- Check for disk space or permission issues

**Workflow fails to push:**
- Verify the repository allows GitHub Actions to push
- Check that branch protection rules don't block GitHub Actions
- Ensure the workflow has write permissions

### Disabling the Workflow

To temporarily disable automatic index regeneration:

1. Rename or delete `.github/workflows/regenerate-indexes.yml`
2. Commit and push the change

To re-enable, restore the file.

### Local vs Automated Workflow

**When to run locally:**
- During development and testing
- When you want immediate feedback
- When working with `--force` flag for full regeneration

**When to rely on the workflow:**
- For standard JAR additions in production
- To ensure consistent index generation
- To maintain audit trail of index changes

Both approaches are valid and produce the same results.
