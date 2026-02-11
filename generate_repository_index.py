#!/usr/bin/env python3
"""
Generate Repository Index for GOSS Archive

Creates OSGi R5 repository index and Maven metadata for GOSS bundles.
Supports dependencies, release, and snapshot repositories.

Usage:
    ./generate_repository_index.py [options] [repository...]

Arguments:
    repository      Repository to process: dependencies, release, snapshot, or all
                   Default: all (processes all three repositories)

Options:
    --force         Force regeneration of all metadata (ignore cache)
    --help          Show this help message

Examples:
    ./generate_repository_index.py                    # Process all repositories
    ./generate_repository_index.py release            # Process only release
    ./generate_repository_index.py release snapshot   # Process release and snapshot
    ./generate_repository_index.py --force all        # Force regenerate everything
"""

import argparse
import gzip
import hashlib
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape as xml_escape


# ANSI color codes
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color


def log_info(msg: str) -> None:
    print(f"{Colors.GREEN}[INFO]{Colors.NC} {msg}")


def log_warn(msg: str) -> None:
    print(f"{Colors.YELLOW}[WARN]{Colors.NC} {msg}")


def log_error(msg: str) -> None:
    print(f"{Colors.RED}[ERROR]{Colors.NC} {msg}")


def log_debug(msg: str) -> None:
    if os.environ.get('DEBUG') == 'true':
        print(f"{Colors.BLUE}[DEBUG]{Colors.NC} {msg}")


@dataclass
class BundleMetadata:
    """OSGi bundle metadata extracted from JAR manifest."""
    symbolic_name: str
    version: str
    name: Optional[str] = None


def extract_bundle_metadata(jar_path: Path) -> Optional[BundleMetadata]:
    """Extract OSGi metadata from JAR manifest."""
    try:
        with zipfile.ZipFile(jar_path, 'r') as zf:
            try:
                manifest_data = zf.read('META-INF/MANIFEST.MF').decode('utf-8', errors='replace')
            except KeyError:
                return None
    except (zipfile.BadZipFile, OSError) as e:
        log_debug(f"  Failed to read JAR {jar_path}: {e}")
        return None

    # Parse manifest - handle line continuations (lines starting with space)
    lines = manifest_data.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    parsed_lines = []
    for line in lines:
        if line.startswith(' ') and parsed_lines:
            # Continuation of previous line
            parsed_lines[-1] += line[1:]
        else:
            parsed_lines.append(line)

    # Extract headers
    headers = {}
    for line in parsed_lines:
        if ':' in line:
            key, _, value = line.partition(':')
            headers[key.strip()] = value.strip()

    bsn = headers.get('Bundle-SymbolicName', '')
    version = headers.get('Bundle-Version', '')
    name = headers.get('Bundle-Name', '')

    # Clean up BSN (remove directives like ;singleton:=true)
    if ';' in bsn:
        bsn = bsn.split(';')[0].strip()

    if not bsn or not version:
        return None

    return BundleMetadata(
        symbolic_name=bsn,
        version=version,
        name=name if name else None
    )


def get_file_sha256(file_path: Path) -> str:
    """Calculate SHA-256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def is_git_repo(path: Path) -> bool:
    """Check if path is within a git repository."""
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--git-dir'],
            cwd=path,
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def get_changed_jars_git(repo_dir: Path) -> list[Path]:
    """Get list of new/modified JAR files using git."""
    try:
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            cwd=repo_dir,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return []

        changed_jars = []
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            # Parse git status output: XY filename
            status = line[:2]
            filename = line[3:]
            if filename.endswith('.jar') and status.strip() in ('??', 'M', 'A', 'AM', 'MM'):
                jar_path = repo_dir / filename
                if jar_path.exists():
                    changed_jars.append(jar_path)

        return changed_jars
    except Exception as e:
        log_debug(f"Git detection failed: {e}")
        return []


def get_all_jars(repo_dir: Path) -> list[Path]:
    """Get all JAR files in repository directory."""
    return list(repo_dir.glob('**/*.jar'))


def generate_bundle_xml(jar_path: Path, metadata: BundleMetadata, repo_dir: Path) -> str:
    """Generate XML for a bundle resource."""
    relative_path = jar_path.relative_to(repo_dir)
    jar_size = jar_path.stat().st_size
    sha256 = get_file_sha256(jar_path)

    # Escape XML special characters
    bsn = xml_escape(metadata.symbolic_name)
    version = xml_escape(metadata.version)

    xml = f'''  <resource>
    <capability namespace="osgi.identity">
      <attribute name="osgi.identity" value="{bsn}"/>
      <attribute name="type" value="osgi.bundle"/>
      <attribute name="version" type="Version" value="{version}"/>
    </capability>
    <capability namespace="osgi.content">
      <attribute name="osgi.content" value="{sha256}"/>
      <attribute name="url" value="{relative_path}"/>
      <attribute name="size" type="Long" value="{jar_size}"/>
      <attribute name="mime" value="application/vnd.osgi.bundle"/>
    </capability>
'''

    if metadata.name:
        name = xml_escape(metadata.name)
        xml += f'''    <capability namespace="osgi.bundle">
      <attribute name="osgi.bundle" value="{bsn}"/>
      <attribute name="bundle-version" type="Version" value="{version}"/>
    </capability>
'''

    xml += '  </resource>'
    return xml


def parse_existing_index(index_path: Path) -> dict[str, str]:
    """Parse existing index.xml to extract bundle entries by URL."""
    if not index_path.exists():
        return {}

    existing_bundles = {}
    try:
        content = index_path.read_text()
        # Extract resource blocks with their URLs
        resource_pattern = re.compile(
            r'<resource>(.*?)</resource>',
            re.DOTALL
        )
        url_pattern = re.compile(r'attribute name="url" value="([^"]+)"')

        for match in resource_pattern.finditer(content):
            resource_xml = match.group(0)
            url_match = url_pattern.search(resource_xml)
            if url_match:
                url = url_match.group(1)
                existing_bundles[url] = resource_xml

    except Exception as e:
        log_warn(f"Failed to parse existing index: {e}")

    return existing_bundles


def generate_osgi_index(repo_dir: Path, force: bool = False) -> None:
    """Generate OSGi R5 repository index."""
    index_path = repo_dir / 'index.xml'
    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')

    log_info(f"Generating OSGi repository index for: {repo_dir}")

    # Determine which JARs to process
    if force or not index_path.exists():
        jars_to_process = get_all_jars(repo_dir)
        existing_bundles = {}
        log_info(f"Processing all {len(jars_to_process)} JAR(s)")
    else:
        existing_bundles = parse_existing_index(index_path)

        # Try git-based change detection
        if is_git_repo(repo_dir):
            jars_to_process = get_changed_jars_git(repo_dir)
            log_info(f"Found {len(jars_to_process)} changed JAR(s) via git")
        else:
            jars_to_process = []

        # Also detect JARs on disk that are missing from the existing index
        all_jars = get_all_jars(repo_dir)
        indexed_urls = set(existing_bundles.keys())
        for jar_path in all_jars:
            relative = str(jar_path.relative_to(repo_dir))
            if relative not in indexed_urls and jar_path not in jars_to_process:
                jars_to_process.append(jar_path)

        if jars_to_process:
            log_info(f"Total JARs to process: {len(jars_to_process)}")
        else:
            # Check for stale entries (indexed but no longer on disk)
            all_jar_paths = {str(j.relative_to(repo_dir)) for j in all_jars}
            stale = [url for url in indexed_urls if url not in all_jar_paths]
            if stale:
                log_info(f"Found {len(stale)} stale index entries to remove")
            else:
                log_info("No changes detected - index is up to date")
                return

    # Build set of changed JAR paths for quick lookup
    changed_paths = {str(jar.relative_to(repo_dir)) for jar in jars_to_process}

    # Generate index
    resources = []
    updated_count = 0
    preserved_count = 0

    # Preserve unchanged bundles from existing index (skip stale and changed)
    for url, resource_xml in existing_bundles.items():
        if url not in changed_paths and (repo_dir / url).exists():
            resources.append(resource_xml)
            preserved_count += 1

    # Process new/changed JARs
    for jar_path in jars_to_process:
        metadata = extract_bundle_metadata(jar_path)
        if metadata:
            resource_xml = generate_bundle_xml(jar_path, metadata, repo_dir)
            resources.append(resource_xml)
            updated_count += 1
            log_info(f"  Updated: {jar_path.name} ({metadata.symbolic_name} {metadata.version})")
        else:
            log_warn(f"  Skipping (no OSGi metadata): {jar_path.name}")

    # Write index
    increment = int(datetime.now(timezone.utc).timestamp() * 1000)
    resources_xml = '\n'.join(resources)
    xml_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<repository increment="{increment}" name="GOSS Archive Repository" xmlns="http://www.osgi.org/xmlns/repository/v1.0.0">
{resources_xml}
</repository>
'''

    index_path.write_text(xml_content)

    # Generate compressed version
    with gzip.open(repo_dir / 'index.xml.gz', 'wt') as f:
        f.write(xml_content)

    log_info(f"  Total bundles: {len(resources)}")
    log_info(f"  New/Updated: {updated_count}")
    log_info(f"  Preserved: {preserved_count}")
    log_info(f"{Colors.GREEN}✓ OSGi index generated: {index_path}{Colors.NC}")


def generate_maven_metadata(artifact_dir: Path) -> None:
    """Generate Maven metadata for an artifact directory."""
    artifact_name = artifact_dir.name
    jars = list(artifact_dir.glob('*.jar'))

    if not jars:
        return

    # Extract versions from JAR filenames
    versions = []
    for jar in jars:
        jar_name = jar.stem
        if jar_name.startswith(artifact_name + '-'):
            version = jar_name[len(artifact_name) + 1:]
            versions.append(version)
        else:
            # Try to extract version from end of filename
            match = re.search(r'-(\d+\.\d+.*?)$', jar_name)
            if match:
                versions.append(match.group(1))

    if not versions:
        return

    # Sort versions (simple string sort, works for most version schemes)
    versions.sort()
    latest_version = versions[-1]
    last_updated = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')

    # Derive groupId and artifactId from artifact name
    # e.g., pnnl.goss.core.core-api -> groupId=pnnl.goss.core, artifactId=core-api
    parts = artifact_name.rsplit('.', 1)
    if len(parts) == 2:
        group_id = parts[0]
        artifact_id = parts[1]
    else:
        group_id = artifact_name
        artifact_id = artifact_name

    versions_xml = '\n'.join(f'      <version>{v}</version>' for v in versions)

    metadata = f'''<?xml version="1.0" encoding="UTF-8"?>
<metadata>
  <groupId>{group_id}</groupId>
  <artifactId>{artifact_id}</artifactId>
  <versioning>
    <latest>{latest_version}</latest>
    <release>{latest_version}</release>
    <versions>
{versions_xml}
    </versions>
    <lastUpdated>{last_updated}</lastUpdated>
  </versioning>
</metadata>
'''

    metadata_path = artifact_dir / 'maven-metadata.xml'
    metadata_path.write_text(metadata)
    log_debug(f"  Generated Maven metadata: {metadata_path}")


def is_maven_metadata_valid(artifact_dir: Path) -> bool:
    """Check if maven-metadata.xml is valid and up-to-date."""
    metadata_path = artifact_dir / 'maven-metadata.xml'
    if not metadata_path.exists():
        return False

    # Get all JAR versions in directory
    artifact_name = artifact_dir.name
    jar_versions = set()
    for jar in artifact_dir.glob('*.jar'):
        jar_name = jar.stem
        if jar_name.startswith(artifact_name + '-'):
            version = jar_name[len(artifact_name) + 1:]
            jar_versions.add(version)

    if not jar_versions:
        return False

    # Check each version is listed in metadata
    try:
        metadata_content = metadata_path.read_text()
        for version in jar_versions:
            if f'<version>{version}</version>' not in metadata_content:
                return False

        # Count versions in metadata
        metadata_version_count = metadata_content.count('<version>')
        if metadata_version_count != len(jar_versions):
            return False

    except Exception:
        return False

    return True


def update_maven_metadata(repo_dir: Path, force: bool = False) -> None:
    """Update Maven metadata for artifact directories."""
    log_info("Updating Maven metadata...")

    # Find all directories containing JARs
    artifact_dirs = set()
    for jar in repo_dir.glob('**/*.jar'):
        artifact_dirs.add(jar.parent)

    updated_count = 0
    skipped_count = 0

    for artifact_dir in sorted(artifact_dirs):
        if force or not is_maven_metadata_valid(artifact_dir):
            generate_maven_metadata(artifact_dir)
            updated_count += 1
            log_info(f"  Updated: {artifact_dir.name}")
        else:
            skipped_count += 1
            log_debug(f"  Skipped (valid): {artifact_dir.name}")

    log_info(f"Updated {updated_count} artifact(s), skipped {skipped_count} (already valid)")


def process_repository(repo_dir: Path, force: bool = False) -> bool:
    """Process a single repository directory."""
    if not repo_dir.exists():
        log_error(f"Repository directory not found: {repo_dir}")
        return False

    if not repo_dir.is_dir():
        log_error(f"Not a directory: {repo_dir}")
        return False

    print()
    log_info("=" * 50)
    log_info(f"Processing repository: {repo_dir.name}")
    log_info("=" * 50)
    print()

    # Update Maven metadata
    update_maven_metadata(repo_dir, force)

    # Generate OSGi repository index
    generate_osgi_index(repo_dir, force)

    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Generate repository indexes for GOSS Archive',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s                      Process all repositories (dependencies, release, snapshot)
  %(prog)s release              Process only the release repository
  %(prog)s release snapshot     Process release and snapshot repositories
  %(prog)s --force all          Force regenerate all indexes
  %(prog)s dependencies         Process only the dependencies repository
'''
    )
    parser.add_argument(
        'repositories',
        nargs='*',
        default=['all'],
        help='Repositories to process: dependencies, release, snapshot, or all (default: all)'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force regeneration of all metadata (ignore cache)'
    )

    args = parser.parse_args()

    # Determine script directory (repository root)
    script_dir = Path(__file__).parent.resolve()

    # Available repositories
    available_repos = {
        'dependencies': script_dir / 'dependencies',
        'release': script_dir / 'release',
        'snapshot': script_dir / 'snapshot',
    }

    # Determine which repositories to process
    repos_to_process = []
    for repo_name in args.repositories:
        if repo_name == 'all':
            repos_to_process = list(available_repos.values())
            break
        elif repo_name in available_repos:
            repos_to_process.append(available_repos[repo_name])
        else:
            log_error(f"Unknown repository: {repo_name}")
            log_error(f"Available: {', '.join(available_repos.keys())}, all")
            return 1

    # Remove duplicates while preserving order
    repos_to_process = list(dict.fromkeys(repos_to_process))

    print()
    log_info("=" * 50)
    log_info("GOSS Repository Index Generator")
    log_info("=" * 50)

    if args.force:
        log_info("Force mode enabled - regenerating all indexes")

    success = True
    for repo_dir in repos_to_process:
        if not process_repository(repo_dir, args.force):
            success = False

    print()
    print(f"{Colors.GREEN}{'=' * 50}{Colors.NC}")
    print(f"{Colors.GREEN}Repository Index Update Complete!{Colors.NC}")
    print(f"  Processed: {', '.join(r.name for r in repos_to_process)}")
    print(f"{Colors.GREEN}{'=' * 50}{Colors.NC}")
    print()

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
