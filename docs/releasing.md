# Releasing

Releases are built from an existing version tag, not the working tree. The tag must have the form `v1.2.3` for a final release or `v1.2.3-rc.1` for a prerelease. Push a tag only after the intended commit and release notes have been reviewed. The release workflow runs tests on both supported Python versions before packaging. It publishes prerelease tags as prereleases, and refuses to update an existing release.

To check the tagged artifacts locally without publishing, use a fresh destination:

```sh
python3 tools/package-release.py v1.2.3 --output /tmp/arche-release-v1.2.3
cd /tmp/arche-release-v1.2.3
shasum -a 256 -c SHA256SUMS
```

Each archive contains `LICENSE`, `INSTALL.md` and a `skills/<name>/` directory, matching the installation layout in the README. The packager reads each file directly from the tag's Git blobs, normalizes ZIP metadata, validates an isolated installation by producing an offline report, and runs the packaged evaluation case listing, checks its pickup fixture, and runs an offline study plan and rescore with isolated Git configuration and no inherited credentials. It rejects symlinks and missing skill definitions. It does not read uncommitted skill files, create tags, or upload anything. The output directory must not already exist; remove a failed local output or choose a new path. ZIP compression can vary across Python/zlib versions, so reproduce published bytes with the same runtime. Verify downloaded artifacts against the published checksums before installation.

The packager rejects hidden, credential-like, cache, and trace paths in skill trees. This path check cannot prove that arbitrary source content contains no private data; review tagged files before release.
