#!/usr/bin/env python3
"""
Script to compare GOSS-Repository dependencies with Maven Central
and update to the latest versions.

Configuration is loaded from dependencies.json which contains:
- bundles: mapping of OSGi bundle-symbolic-name to Maven coordinates
- additionalDownloads: list of additional artifacts to download directly

Usage:
  python3 update_dependencies.py              # Download updates
  python3 update_dependencies.py --check-only # Only check for updates
  python3 update_dependencies.py --sync       # Sync all dependencies from dependencies.json
  python3 update_dependencies.py --regenerate # Only regenerate index
"""

import os
import re
import json
import subprocess
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
import time
import argparse
import gzip
import hashlib
from pathlib import Path
from typing import Optional, Dict, List, Tuple

REPO_ROOT = Path(__file__).parent
REPO_DIR = REPO_ROOT / "dependencies"
INDEX_FILE = REPO_ROOT / "index.xml"
CONFIG_FILE = REPO_ROOT / "dependencies.json"
BND_JAR = REPO_DIR / "biz.aQute.bnd" / "biz.aQute.bnd-7.1.0.jar"
MAVEN_SEARCH_URL = "https://search.maven.org/solrsearch/select"
MAVEN_DOWNLOAD_URL = "https://repo1.maven.org/maven2"
BND_HUB_URL = "https://raw.githubusercontent.com/bndtools/bundle-hub/master"

# Additional Maven repositories
MAVEN_REPOSITORIES = {
    'Maven Central': 'https://repo1.maven.org/maven2',
    'Spring Plugins': 'https://repo.spring.io/plugins-release',
    'Spring Libs': 'https://repo.spring.io/libs-release',
    'JBoss': 'https://repository.jboss.org/nexus/content/repositories/releases',
    'Sonatype': 'https://oss.sonatype.org/content/repositories/releases',
}

# OSGi namespace
NS = {'repo': 'http://www.osgi.org/xmlns/repository/v1.0.0'}

def load_config() -> Tuple[Dict, List[Dict]]:
    """Load configuration from dependencies.json."""
    if not CONFIG_FILE.exists():
        print(f"Error: Configuration file not found: {CONFIG_FILE}")
        print("Please create dependencies.json with bundle mappings.")
        return {}, []

    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)

    # Convert bundles to the format expected by the script
    bundle_to_maven = {}
    for bundle_name, mapping in config.get('bundles', {}).items():
        if mapping.get('local', False):
            bundle_to_maven[bundle_name] = None
        elif 'groupId' in mapping and 'artifactId' in mapping:
            bundle_to_maven[bundle_name] = (mapping['groupId'], mapping['artifactId'])
        else:
            bundle_to_maven[bundle_name] = None

    additional_downloads = config.get('additionalDownloads', [])

    return bundle_to_maven, additional_downloads

# Load configuration
BUNDLE_TO_MAVEN, ADDITIONAL_DOWNLOADS = load_config()

def parse_index_xml() -> List[Dict]:
    """Parse the index.xml file and extract bundle information."""
    tree = ET.parse(INDEX_FILE)
    root = tree.getroot()

    bundles = []
    for resource in root.findall('repo:resource', NS):
        bundle_info = {
            'identity': None,
            'version': None,
            'url': None,
            'type': None,
        }

        for capability in resource.findall('repo:capability', NS):
            namespace = capability.get('namespace')

            if namespace == 'osgi.identity':
                for attr in capability.findall('repo:attribute', NS):
                    name = attr.get('name')
                    if name == 'osgi.identity':
                        bundle_info['identity'] = attr.get('value')
                    elif name == 'version':
                        bundle_info['version'] = attr.get('value')
                    elif name == 'type':
                        bundle_info['type'] = attr.get('value')

            elif namespace == 'osgi.content':
                for attr in capability.findall('repo:attribute', NS):
                    name = attr.get('name')
                    if name == 'url':
                        bundle_info['url'] = attr.get('value')

        if bundle_info['identity'] and bundle_info['version']:
            bundles.append(bundle_info)

    return bundles

def get_latest_version_from_maven(group_id: str, artifact_id: str) -> Optional[Dict]:
    """Query Maven Central for the latest version of an artifact."""
    query = f'g:"{group_id}" AND a:"{artifact_id}"'
    url = f"{MAVEN_SEARCH_URL}?q={urllib.parse.quote(query)}&rows=1&wt=json"

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'GOSS-Repository-Updater/1.0'})
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
            if data['response']['numFound'] > 0:
                doc = data['response']['docs'][0]
                return {
                    'version': doc.get('latestVersion', doc.get('v')),
                    'group_id': doc['g'],
                    'artifact_id': doc['a'],
                }
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, KeyError) as e:
        print(f"  Error querying Maven for {group_id}:{artifact_id}: {e}")
    return None

def download_jar(group_id: str, artifact_id: str, version: str, dest_dir: Path, repo_url: str = None) -> bool:
    """Download a JAR from a Maven repository."""
    if repo_url is None:
        repo_url = MAVEN_DOWNLOAD_URL

    group_path = group_id.replace('.', '/')
    jar_name = f"{artifact_id}-{version}.jar"
    url = f"{repo_url}/{group_path}/{artifact_id}/{version}/{jar_name}"

    # Ensure destination directory exists
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / jar_name

    try:
        print(f"  Downloading from: {url}")
        req = urllib.request.Request(url, headers={'User-Agent': 'GOSS-Repository-Updater/1.0'})
        with urllib.request.urlopen(req, timeout=60) as response:
            data = response.read()
            # Check if we got a valid JAR (not an error page)
            if len(data) < 1000 and b'<html' in data.lower():
                print(f"  Error: Received HTML instead of JAR")
                return False
            with open(dest_path, 'wb') as f:
                f.write(data)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"  Error downloading {jar_name}: {e}")
        return False

def download_jar_from_multiple_repos(group_id: str, artifact_id: str, version: str, dest_dir: Path) -> Tuple[bool, str]:
    """Try downloading a JAR from multiple Maven repositories."""
    for repo_name, repo_url in MAVEN_REPOSITORIES.items():
        print(f"  Trying {repo_name}...")
        if download_jar(group_id, artifact_id, version, dest_dir, repo_url):
            return True, repo_name
    return False, None

def search_mvnrepository(group_id: str, artifact_id: str, version: str = None) -> Optional[Dict]:
    """Search mvnrepository.com for artifact information and repository URLs."""
    if version:
        url = f"https://mvnrepository.com/artifact/{group_id}/{artifact_id}/{version}"
    else:
        url = f"https://mvnrepository.com/artifact/{group_id}/{artifact_id}"

    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=30) as response:
            html = response.read().decode('utf-8')
            import re

            result = {
                'group_id': group_id,
                'artifact_id': artifact_id,
            }

            # Find latest version if not specified
            if not version:
                version_match = re.search(r'<a[^>]*class="vbtn release"[^>]*>([^<]+)</a>', html)
                if version_match:
                    result['version'] = version_match.group(1).strip()
                else:
                    return None
            else:
                result['version'] = version

            # Look for repository information
            # mvnrepository shows which repos host the artifact
            repo_urls = []

            # Check for different repository patterns
            if 'repo1.maven.org' in html or 'Maven Central' in html:
                repo_urls.append(('Maven Central', 'https://repo1.maven.org/maven2'))
            if 'repository.spring.io' in html or 'Spring' in html:
                repo_urls.append(('Spring', 'https://repo.spring.io/plugins-release'))
            if 'repository.jboss.org' in html:
                repo_urls.append(('JBoss', 'https://repository.jboss.org/nexus/content/repositories/releases'))

            # Look for direct download link
            download_match = re.search(r'href="([^"]*\.jar)"[^>]*>.*?jar.*?</a>', html, re.IGNORECASE)
            if download_match:
                result['direct_url'] = download_match.group(1)

            result['repositories'] = repo_urls if repo_urls else [('Maven Central', 'https://repo1.maven.org/maven2')]

            return result
    except Exception as e:
        print(f"  Error searching mvnrepository.com: {e}")
    return None

def download_from_mvnrepository(group_id: str, artifact_id: str, version: str, dest_dir: Path) -> Tuple[bool, str]:
    """Download artifact using information from mvnrepository.com."""
    print(f"  Searching mvnrepository.com for {group_id}:{artifact_id}:{version}...")

    info = search_mvnrepository(group_id, artifact_id, version)
    if not info:
        return False, None

    # Try repositories listed on mvnrepository
    for repo_name, repo_url in info.get('repositories', []):
        print(f"  Trying {repo_name} ({repo_url})...")
        if download_jar(group_id, artifact_id, version, dest_dir, repo_url):
            return True, repo_name

    return False, None

def get_latest_version_from_bnd_hub(bundle_name: str) -> Optional[str]:
    """Query BND Hub (GitHub) for the latest version of a bundle."""
    # Use GitHub API to list contents of the bundle directory
    api_url = f"https://api.github.com/repos/bndtools/bundle-hub/contents/{bundle_name}"

    try:
        req = urllib.request.Request(api_url, headers={
            'User-Agent': 'GOSS-Repository-Updater/1.0',
            'Accept': 'application/vnd.github.v3+json'
        })
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())

            # Find all JAR files and extract versions
            versions = []
            for item in data:
                if item['type'] == 'file' and item['name'].endswith('.jar'):
                    # Extract version from filename like "osgi.residential-4.3.0.jar"
                    name = item['name']
                    if name.startswith(bundle_name + '-') and name.endswith('.jar'):
                        version = name[len(bundle_name) + 1:-4]  # Remove prefix and .jar
                        versions.append(version)

            if versions:
                # Sort versions and return the latest
                versions.sort(key=lambda v: [int(x) if x.isdigit() else x for x in re.split(r'[._-]', v)], reverse=True)
                return versions[0]
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        pass
    return None


def download_from_bnd_hub(bundle_name: str, version: str, dest_dir: Path) -> bool:
    """Download a JAR from BND Hub."""
    jar_name = f"{bundle_name}-{version}.jar"
    url = f"{BND_HUB_URL}/{bundle_name}/{jar_name}"

    # Ensure destination directory exists
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / jar_name

    try:
        print(f"  Downloading from BND Hub: {url}")
        req = urllib.request.Request(url, headers={'User-Agent': 'GOSS-Repository-Updater/1.0'})
        with urllib.request.urlopen(req, timeout=60) as response:
            with open(dest_path, 'wb') as f:
                f.write(response.read())
        return True
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"  Error downloading {jar_name} from BND Hub: {e}")
        return False

def regenerate_index() -> bool:
    """Regenerate the BND repository index."""
    print("\n" + "="*60)
    print("REGENERATING REPOSITORY INDEX")
    print("="*60 + "\n")

    if not BND_JAR.exists():
        print(f"Error: BND JAR not found at {BND_JAR}")
        return False

    # Find all JAR files
    jar_files = []
    for folder in ['dependencies', 'release', 'snapshot']:
        folder_path = REPO_ROOT / folder
        if folder_path.exists():
            jar_files.extend(folder_path.rglob('*.jar'))

    print(f"Found {len(jar_files)} JAR files")

    # Build command
    cmd = [
        'java', '-jar', str(BND_JAR),
        'index', '-r', str(INDEX_FILE),
        '-n', 'GOSS Dependencies'
    ] + [str(f) for f in jar_files]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
        if result.returncode == 0:
            print(f"Index regenerated successfully: {INDEX_FILE}")
            # Count resources
            if INDEX_FILE.exists():
                with open(INDEX_FILE, 'r') as f:
                    content = f.read()
                    resource_count = content.count('<resource')
                    print(f"Total resources in index: {resource_count}")

                # Create gzipped version
                gz_file = REPO_ROOT / "index.xml.gz"
                with open(INDEX_FILE, 'rb') as f_in:
                    with gzip.open(gz_file, 'wb') as f_out:
                        f_out.writelines(f_in)
                print(f"Created gzipped index: {gz_file}")

                # Create SHA hash
                sha_file = REPO_ROOT / "index.xml.sha"
                with open(INDEX_FILE, 'rb') as f:
                    sha256 = hashlib.sha256(f.read()).hexdigest()
                with open(sha_file, 'w') as f:
                    f.write(sha256)
                print(f"Created SHA hash: {sha_file}")

            return True
        else:
            print(f"Error regenerating index: {result.stderr}")
            return False
    except Exception as e:
        print(f"Exception regenerating index: {e}")
        return False

def compare_versions(v1: str, v2: str) -> int:
    """Compare two version strings. Returns -1 if v1 < v2, 0 if equal, 1 if v1 > v2."""
    def normalize(v):
        # Remove common suffixes and split
        v = re.sub(r'[-_](RELEASE|FINAL|GA)$', '', v, flags=re.IGNORECASE)
        parts = re.split(r'[._-]', v)
        result = []
        for p in parts:
            try:
                result.append(int(p))
            except ValueError:
                result.append(p)
        return result

    n1, n2 = normalize(v1), normalize(v2)
    for a, b in zip(n1, n2):
        if isinstance(a, int) and isinstance(b, int):
            if a < b:
                return -1
            elif a > b:
                return 1
        else:
            if str(a) < str(b):
                return -1
            elif str(a) > str(b):
                return 1
    if len(n1) < len(n2):
        return -1
    elif len(n1) > len(n2):
        return 1
    return 0

def get_dest_dir_from_url(url: str) -> Path:
    """Get the destination directory from the URL path."""
    # URL format: "folder/filename.jar"
    parts = url.split('/')
    if len(parts) >= 1:
        return REPO_DIR / parts[0]
    return REPO_DIR

def download_additional_bundles(additional_downloads: List[Dict], check_existing: bool = False) -> Dict:
    """Download additional bundles specified in the configuration.

    Args:
        additional_downloads: List of dependencies to download
        check_existing: If True, skip files that already exist
    """
    results = {
        'downloaded': [],
        'errors': [],
        'already_exists': [],
    }

    if not additional_downloads:
        return results

    print("\n" + "="*60)
    print("DOWNLOADING ADDITIONAL BUNDLES")
    print("="*60 + "\n")

    for item in additional_downloads:
        # Skip comment entries
        if '_comment' in item and 'groupId' not in item:
            continue

        group_id = item.get('groupId')
        artifact_id = item.get('artifactId')
        folder = item.get('folder', 'misc')
        pinned_version = item.get('version')  # Optional pinned version
        source = item.get('source', 'Maven Central')  # Source repository

        if not group_id or not artifact_id:
            continue

        print(f"Processing: {group_id}:{artifact_id}")

        # Handle BND Hub downloads differently
        if source == 'BND Hub':
            bundle_name = artifact_id
            version = pinned_version or '4.3.0'  # Default version for BND Hub
            print(f"  Source: BND Hub, version: {version}")

            dest_dir = REPO_DIR / folder
            jar_name = f"{bundle_name}-{version}.jar"
            dest_path = dest_dir / jar_name

            # Check if already exists
            if check_existing and dest_path.exists():
                print(f"  Already exists: {jar_name}")
                results['already_exists'].append({
                    'group_id': group_id,
                    'artifact_id': artifact_id,
                    'version': version,
                    'folder': folder,
                })
                continue

            if download_from_bnd_hub(bundle_name, version, dest_dir):
                print(f"  Downloaded: {bundle_name}-{version}.jar to {folder}/")
                results['downloaded'].append({
                    'group_id': group_id,
                    'artifact_id': artifact_id,
                    'version': version,
                    'folder': folder,
                    'source': 'BND Hub'
                })
            else:
                results['errors'].append({
                    'group_id': group_id,
                    'artifact_id': artifact_id,
                    'reason': f'Failed to download from BND Hub'
                })
            time.sleep(0.3)
            continue

        # Use pinned version if specified, otherwise query Maven Central
        if pinned_version:
            latest_version = pinned_version
            print(f"  Pinned version: {latest_version}")
        else:
            # Query Maven Central for latest version
            maven_info = get_latest_version_from_maven(group_id, artifact_id)

            if maven_info is None:
                # Try mvnrepository as fallback
                print(f"  Not found on Maven Central, trying mvnrepository...")
                maven_info = search_mvnrepository(group_id, artifact_id)

            if maven_info is None:
                print(f"  NOT FOUND on Maven Central or mvnrepository")
                results['errors'].append({
                    'group_id': group_id,
                    'artifact_id': artifact_id,
                    'reason': 'Not found on Maven Central or mvnrepository'
                })
                time.sleep(0.3)
                continue

            latest_version = maven_info['version']
            print(f"  Latest version: {latest_version}")

        # Check if already exists
        dest_dir = REPO_DIR / folder
        jar_name = f"{artifact_id}-{latest_version}.jar"
        dest_path = dest_dir / jar_name

        if check_existing and dest_path.exists():
            print(f"  Already exists: {jar_name}")
            results['already_exists'].append({
                'group_id': group_id,
                'artifact_id': artifact_id,
                'version': latest_version,
                'folder': folder,
            })
            continue

        # Download to specified folder
        repo_url = item.get('repoUrl')  # Custom repository URL

        if repo_url:
            # Use custom repository URL
            print(f"  Using custom repository: {repo_url}")
            success = download_jar(group_id, artifact_id, latest_version, dest_dir, repo_url)
            actual_source = repo_url
        else:
            # Try Maven Central first, then other repositories
            success = download_jar(group_id, artifact_id, latest_version, dest_dir)
            actual_source = 'Maven Central'

            if not success:
                # Try other repositories
                print(f"  Maven Central failed, trying other repositories...")
                success, repo_name = download_jar_from_multiple_repos(group_id, artifact_id, latest_version, dest_dir)
                if success:
                    actual_source = repo_name

        if success:
            print(f"  Downloaded: {artifact_id}-{latest_version}.jar to {folder}/ from {actual_source}")
            results['downloaded'].append({
                'group_id': group_id,
                'artifact_id': artifact_id,
                'version': latest_version,
                'folder': folder,
                'source': actual_source
            })
        else:
            results['errors'].append({
                'group_id': group_id,
                'artifact_id': artifact_id,
                'reason': f'Failed to download {latest_version} from any repository'
            })

        # Rate limiting
        time.sleep(0.3)

    return results


def check_for_updates():
    """Check all dependencies for available updates without downloading."""
    print(f"Configuration loaded from: {CONFIG_FILE}")
    print(f"Additional downloads: {len(ADDITIONAL_DOWNLOADS)}\n")

    updates = []
    up_to_date = []
    errors = []
    skipped = []

    print("Checking additional downloads for updates...")
    print("-" * 100)

    for item in ADDITIONAL_DOWNLOADS:
        # Skip comment entries
        if '_comment' in item and 'groupId' not in item:
            continue

        group_id = item.get('groupId')
        artifact_id = item.get('artifactId')
        current_version = item.get('version')
        source = item.get('source', 'Maven Central')

        if not group_id or not artifact_id:
            continue

        # Handle BND Hub items separately
        if source == 'BND Hub':
            latest = get_latest_version_from_bnd_hub(artifact_id)
            if latest is None:
                errors.append({
                    'group_id': group_id,
                    'artifact_id': artifact_id,
                    'current': current_version,
                    'reason': 'Not found on BND Hub'
                })
                time.sleep(0.2)
                continue
        else:
            # Get latest version from Maven
            maven_info = get_latest_version_from_maven(group_id, artifact_id)
            latest = maven_info['version'] if maven_info else None

            if latest is None:
                # Try mvnrepository as fallback
                mvn_info = search_mvnrepository(group_id, artifact_id)
                if mvn_info:
                    latest = mvn_info.get('version')

        if latest is None:
            errors.append({
                'group_id': group_id,
                'artifact_id': artifact_id,
                'current': current_version,
                'reason': 'Not found'
            })
            time.sleep(0.2)
            continue

        # Compare versions
        if current_version:
            cmp = compare_versions(current_version, latest)
            if cmp < 0:
                updates.append({
                    'group_id': group_id,
                    'artifact_id': artifact_id,
                    'current_version': current_version,
                    'latest_version': latest,
                    'folder': item.get('folder', 'misc'),
                    'comment': item.get('_comment', '')
                })
            else:
                up_to_date.append({
                    'group_id': group_id,
                    'artifact_id': artifact_id,
                    'version': current_version
                })
        else:
            # No current version pinned
            updates.append({
                'group_id': group_id,
                'artifact_id': artifact_id,
                'current_version': 'unpinned',
                'latest_version': latest,
                'folder': item.get('folder', 'misc'),
                'comment': item.get('_comment', '')
            })

        time.sleep(0.2)

    # Print results
    print("\n" + "=" * 110)
    print("AVAILABLE UPDATES")
    print("=" * 110)

    if updates:
        # Table header
        print(f"\n{'Group ID':<45} {'Artifact ID':<30} {'Current':<15} {'Latest':<15}")
        print("-" * 110)

        for u in sorted(updates, key=lambda x: f"{x['group_id']}:{x['artifact_id']}"):
            group = u['group_id'][:44]
            artifact = u['artifact_id'][:29]
            current = u['current_version'][:14]
            latest = u['latest_version'][:14]
            print(f"{group:<45} {artifact:<30} {current:<15} {latest:<15}")

        print(f"\nTotal updates available: {len(updates)}")
    else:
        print("\nAll dependencies are up to date!")

    # Print summary
    print(f"\n{'=' * 110}")
    print("SUMMARY")
    print("=" * 110)
    print(f"Updates available: {len(updates)}")
    print(f"Up to date: {len(up_to_date)}")
    if skipped:
        print(f"Skipped: {len(skipped)}")
    print(f"Errors: {len(errors)}")

    # Print errors if any
    if errors:
        print(f"\n{'=' * 110}")
        print("ERRORS (dependencies not found)")
        print("=" * 110)
        for e in errors:
            print(f"  - {e.get('group_id', '')}:{e.get('artifact_id', '')} - {e['reason']}")

    return updates


def sync_dependencies():
    """Sync all dependencies from dependencies.json, downloading only missing ones."""
    print(f"Configuration loaded from: {CONFIG_FILE}")
    print(f"Additional downloads: {len(ADDITIONAL_DOWNLOADS)}\n")

    print("="*60)
    print("SYNCING DEPENDENCIES FROM dependencies.json")
    print("="*60)
    print("\nThis will download all dependencies listed in additionalDownloads")
    print("that are not already present in the repository.\n")

    # Download additional bundles, checking for existing files
    results = download_additional_bundles(ADDITIONAL_DOWNLOADS, check_existing=True)

    # Generate summary report
    print("\n" + "="*60)
    print("SYNC SUMMARY")
    print("="*60)

    if results['downloaded']:
        print(f"\nDownloaded: {len(results['downloaded'])}")
        for item in results['downloaded']:
            print(f"  - {item['group_id']}:{item['artifact_id']}:{item['version']} -> {item['folder']}/")

    if results['already_exists']:
        print(f"\nAlready exists: {len(results['already_exists'])}")
        for item in results['already_exists']:
            print(f"  - {item['group_id']}:{item['artifact_id']}:{item['version']} in {item['folder']}/")

    if results['errors']:
        print(f"\nErrors: {len(results['errors'])}")
        for item in results['errors']:
            print(f"  - {item['group_id']}:{item['artifact_id']}: {item['reason']}")

    total_requested = len(ADDITIONAL_DOWNLOADS) - sum(1 for item in ADDITIONAL_DOWNLOADS if '_comment' in item and 'groupId' not in item)
    total_available = len(results['downloaded']) + len(results['already_exists'])

    print(f"\nTotal dependencies requested: {total_requested}")
    print(f"Total dependencies available: {total_available}")
    print(f"Coverage: {100.0 * total_available / total_requested if total_requested > 0 else 0:.1f}%")

    # Regenerate the repository index
    if results['downloaded']:
        print("\nRebuilding repository index...")
        regenerate_index()
    else:
        print("\nNo new dependencies downloaded, skipping index rebuild.")

    return results


def main():
    parser = argparse.ArgumentParser(
        description='Update GOSS-Repository dependencies from Maven Central and other sources'
    )
    parser.add_argument(
        '--check-only',
        action='store_true',
        help='Only check for updates, do not download'
    )
    parser.add_argument(
        '--regenerate',
        action='store_true',
        help='Only regenerate the repository index'
    )
    parser.add_argument(
        '--sync',
        action='store_true',
        help='Sync all dependencies from dependencies.json (download missing ones only)'
    )

    args = parser.parse_args()

    # Handle --regenerate
    if args.regenerate:
        regenerate_index()
        return

    # Handle --check-only
    if args.check_only:
        check_for_updates()
        return

    # Handle --sync
    if args.sync:
        sync_dependencies()
        return

    # Default behavior: full update
    print(f"Configuration loaded from: {CONFIG_FILE}")
    print(f"Bundle mappings: {len(BUNDLE_TO_MAVEN)}")
    print(f"Additional downloads: {len(ADDITIONAL_DOWNLOADS)}\n")

    if not INDEX_FILE.exists():
        print(f"Index file not found: {INDEX_FILE}")
        print("Will only process additional downloads.\n")
        bundles = []
    else:
        print("Parsing index.xml...")
        bundles = parse_index_xml()
        print(f"Found {len(bundles)} bundle entries\n")

    # Group bundles by identity to find the highest version of each
    bundle_versions = {}
    for bundle in bundles:
        identity = bundle['identity']
        if identity not in bundle_versions:
            bundle_versions[identity] = []
        bundle_versions[identity].append(bundle)

    # Sort each bundle's versions and keep the highest
    latest_bundles = {}
    for identity, versions in bundle_versions.items():
        versions.sort(key=lambda x: x['version'], reverse=True)
        latest_bundles[identity] = versions[0]

    print(f"Found {len(latest_bundles)} unique bundles\n")

    results = {
        'updated': [],
        'up_to_date': [],
        'unavailable': [],
        'local_only': [],
        'not_mapped': [],
        'errors': [],
    }

    for identity, bundle in sorted(latest_bundles.items()):
        local_version = bundle['version']
        url = bundle['url']

        print(f"Processing: {identity}")
        print(f"  Local version: {local_version}")

        # Get Maven coordinates
        if identity not in BUNDLE_TO_MAVEN:
            print(f"  NOT MAPPED - no Maven coordinates defined")
            results['not_mapped'].append({
                'identity': identity,
                'local_version': local_version,
                'url': url
            })
            continue

        coords = BUNDLE_TO_MAVEN[identity]
        if coords is None:
            print(f"  LOCAL/CUSTOM - not on Maven Central")
            results['local_only'].append({
                'identity': identity,
                'local_version': local_version,
                'url': url
            })
            continue

        group_id, artifact_id = coords
        print(f"  Maven coordinates: {group_id}:{artifact_id}")

        # Query Maven Central
        maven_info = get_latest_version_from_maven(group_id, artifact_id)

        if maven_info is None:
            print(f"  NOT FOUND on Maven Central")
            results['unavailable'].append({
                'identity': identity,
                'group_id': group_id,
                'artifact_id': artifact_id,
                'local_version': local_version,
                'url': url
            })
            time.sleep(0.3)
            continue

        latest_version = maven_info['version']
        print(f"  Maven latest: {latest_version}")

        # Compare versions
        cmp = compare_versions(local_version, latest_version)

        if cmp >= 0:
            print(f"  UP TO DATE")
            results['up_to_date'].append({
                'identity': identity,
                'group_id': group_id,
                'artifact_id': artifact_id,
                'version': local_version
            })
        else:
            print(f"  NEEDS UPDATE: {local_version} -> {latest_version}")

            # Download new version
            dest_dir = get_dest_dir_from_url(url)
            if download_jar(group_id, artifact_id, latest_version, dest_dir):
                print(f"  Downloaded: {artifact_id}-{latest_version}.jar")
                results['updated'].append({
                    'identity': identity,
                    'group_id': group_id,
                    'artifact_id': artifact_id,
                    'old_version': local_version,
                    'new_version': latest_version
                })
            else:
                results['errors'].append({
                    'identity': identity,
                    'reason': f'Failed to download {latest_version}',
                    'group_id': group_id,
                    'artifact_id': artifact_id
                })

        # Rate limiting
        time.sleep(0.3)

    # Download additional bundles from configuration
    additional_results = download_additional_bundles(ADDITIONAL_DOWNLOADS)

    # Generate report
    print("\n" + "="*60)
    print("SUMMARY REPORT")
    print("="*60)

    print(f"\nUpdated: {len(results['updated'])}")
    for item in results['updated']:
        print(f"  - {item['group_id']}:{item['artifact_id']} {item['old_version']} -> {item['new_version']}")

    print(f"\nUp to date: {len(results['up_to_date'])}")

    print(f"\nUnavailable on Maven Central: {len(results['unavailable'])}")
    for item in results['unavailable']:
        print(f"  - {item['group_id']}:{item['artifact_id']} (local: {item['local_version']})")

    print(f"\nLocal/Custom artifacts: {len(results['local_only'])}")
    for item in results['local_only']:
        print(f"  - {item['identity']} (version: {item['local_version']})")

    print(f"\nNot mapped (need Maven coordinates): {len(results['not_mapped'])}")
    for item in results['not_mapped']:
        print(f"  - {item['identity']} (version: {item['local_version']})")

    print(f"\nErrors: {len(results['errors'])}")
    for item in results['errors']:
        print(f"  - {item['identity']}: {item['reason']}")

    # Additional downloads summary
    if additional_results['downloaded']:
        print(f"\nAdditional bundles downloaded: {len(additional_results['downloaded'])}")
        for item in additional_results['downloaded']:
            print(f"  - {item['group_id']}:{item['artifact_id']}:{item['version']} -> {item['folder']}/")

    if additional_results['errors']:
        print(f"\nAdditional download errors: {len(additional_results['errors'])}")
        for item in additional_results['errors']:
            print(f"  - {item['group_id']}:{item['artifact_id']}: {item['reason']}")

    # Write unavailable dependencies report
    report_path = REPO_DIR.parent / "unavailable_dependencies.md"
    with open(report_path, 'w') as f:
        f.write("# Unavailable Dependencies Report\n\n")
        f.write("Generated by update_dependencies.py\n\n")

        f.write("## Unavailable on Maven Central\n\n")
        if results['unavailable']:
            f.write("These dependencies could not be found on Maven Central with the mapped coordinates.\n\n")
            f.write("| Bundle Identity | Group ID | Artifact ID | Local Version |\n")
            f.write("|-----------------|----------|-------------|---------------|\n")
            for item in results['unavailable']:
                f.write(f"| {item['identity']} | {item['group_id']} | {item['artifact_id']} | {item['local_version']} |\n")
        else:
            f.write("None\n")

        f.write("\n## Custom/Local Artifacts\n\n")
        if results['local_only']:
            f.write("These are project-specific or custom artifacts not published to Maven Central.\n\n")
            f.write("| Bundle Identity | Local Version | URL |\n")
            f.write("|-----------------|---------------|-----|\n")
            for item in results['local_only']:
                f.write(f"| {item['identity']} | {item['local_version']} | {item['url']} |\n")
        else:
            f.write("None\n")

        f.write("\n## Not Mapped\n\n")
        if results['not_mapped']:
            f.write("These bundles need Maven coordinates added to the BUNDLE_TO_MAVEN mapping.\n\n")
            f.write("| Bundle Identity | Local Version | URL |\n")
            f.write("|-----------------|---------------|-----|\n")
            for item in results['not_mapped']:
                f.write(f"| {item['identity']} | {item['local_version']} | {item['url']} |\n")
        else:
            f.write("None\n")

        f.write("\n## Errors\n\n")
        if results['errors']:
            f.write("| Bundle Identity | Reason |\n")
            f.write("|-----------------|--------|\n")
            for item in results['errors']:
                f.write(f"| {item['identity']} | {item['reason']} |\n")
        else:
            f.write("None\n")

        f.write("\n## Successfully Updated\n\n")
        if results['updated']:
            f.write("| Group ID | Artifact ID | Old Version | New Version |\n")
            f.write("|----------|-------------|-------------|-------------|\n")
            for item in results['updated']:
                f.write(f"| {item['group_id']} | {item['artifact_id']} | {item['old_version']} | {item['new_version']} |\n")
        else:
            f.write("None\n")

        f.write("\n## Up to Date\n\n")
        f.write(f"{len(results['up_to_date'])} dependencies are already at their latest version.\n\n")
        if results['up_to_date']:
            f.write("| Group ID | Artifact ID | Version |\n")
            f.write("|----------|-------------|----------|\n")
            for item in results['up_to_date']:
                f.write(f"| {item['group_id']} | {item['artifact_id']} | {item['version']} |\n")

        f.write("\n## Additional Downloads\n\n")
        if additional_results['downloaded']:
            f.write("Additional bundles downloaded from dependencies.json configuration.\n\n")
            f.write("| Group ID | Artifact ID | Version | Folder |\n")
            f.write("|----------|-------------|---------|--------|\n")
            for item in additional_results['downloaded']:
                f.write(f"| {item['group_id']} | {item['artifact_id']} | {item['version']} | {item['folder']} |\n")
        else:
            f.write("None\n")

        if additional_results['errors']:
            f.write("\n### Additional Download Errors\n\n")
            f.write("| Group ID | Artifact ID | Reason |\n")
            f.write("|----------|-------------|--------|\n")
            for item in additional_results['errors']:
                f.write(f"| {item['group_id']} | {item['artifact_id']} | {item['reason']} |\n")

    print(f"\nReport written to: {report_path}")

    # Regenerate the repository index
    regenerate_index()

if __name__ == "__main__":
    main()
