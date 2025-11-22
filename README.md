# GOSS-Repository

An OSGi bundle repository that can be used by BND tools to distribute required bundles for the GridAPPS-D platform.

## Repository Structure

- `dependencies/` - OSGi bundles organized by artifact (e.g., `org.apache.jena.osgi/`, `activemq/`)
- `cnf/releaserepo/` - Release repository for versioned bundles
- `index.xml` / `index.xml.gz` - OSGi R5 repository index (auto-generated)
- `generate-repository-index.sh` - Script to regenerate repository indexes

## Adding New Bundles

1. Copy your bundle JAR to the appropriate directory:
   ```bash
   cp mybundle-1.0.0.jar dependencies/com.example.mybundle/
   ```

2. Regenerate the repository index:
   ```bash
   ./generate-repository-index.sh
   ```

3. Commit and push your changes:
   ```bash
   git add dependencies/com.example.mybundle/
   git add index.xml index.xml.gz
   git commit -m "Add mybundle 1.0.0"
   git push
   ```

The repository index will be automatically regenerated via GitHub Actions when JAR files are pushed to the master branch.

## Index Generation

The `generate-repository-index.sh` script:
- Detects new/modified JAR files via git
- Extracts OSGi metadata from changed bundles only
- Preserves existing entries for unchanged bundles
- Generates OSGi R5 repository index (`index.xml`)
- Creates compressed index (`index.xml.gz`)
- Updates Maven metadata for each artifact

**Force full regeneration:**
```bash
./generate-repository-index.sh --force
```

See [INDEXING.md](INDEXING.md) for detailed documentation.

## Documentation

- [Index Generation Guide](INDEXING.md) - Detailed documentation for the index generation script
- [Wiki](https://github.com/GridOPTICS/GOSS-Repository/wiki) - Further documentation
