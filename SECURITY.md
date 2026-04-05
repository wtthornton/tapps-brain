# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 2.x     | Yes       |
| 1.x     | No        |

Only the latest 2.x release receives security patches. Upgrade to the latest release before reporting vulnerabilities.

## CVE response timelines

When a CVE is published against a direct or transitive dependency of tapps-brain, the maintainers follow these timelines:

| Severity | Target resolution | Notes |
|----------|-------------------|-------|
| **Critical** (CVSS 9.0+) | **7 calendar days** | Patch release with bumped dependency; advisory posted |
| **High** (CVSS 7.0-8.9) | **14 calendar days** | Patch release |
| **Moderate** (CVSS 4.0-6.9) | **30 calendar days** | Next scheduled release |
| **Low** (CVSS < 4.0) | Next minor release | Bundled with feature work |

"Resolution" means a release is published with the vulnerable dependency bumped or replaced. If an upstream fix is not yet available, the advisory will document workarounds and the tracking issue will remain open.

## SBOM (Software Bill of Materials)

Enterprise consumers who require an SBOM can generate one locally using [CycloneDX](https://github.com/CycloneDX/cyclonedx-python):

```bash
pip install cyclonedx-bom
cyclonedx-py environment -o sbom.json --output-format json
```

Or from the locked dependency graph:

```bash
cyclonedx-py requirements -i requirements.txt -o sbom.xml
```

SBOM generation is not yet automated in CI. This is tracked for future release workflow integration. When added, SBOM artifacts will be attached to GitHub Releases.

## Responsible disclosure

If you discover a security vulnerability in tapps-brain, please report it responsibly:

1. **Preferred:** Open a [GitHub Security Advisory](https://github.com/wtthornton/tapps-brain/security/advisories/new) (private by default).
2. **Alternative:** Email the maintainers at the address listed in the repository's GitHub profile.
3. **Public issues:** If the vulnerability is in a third-party dependency and already has a public CVE, you may open a regular GitHub issue linking the CVE.

Please **do not** open a public issue for vulnerabilities in tapps-brain's own code before the maintainers have had a chance to assess and patch.

## Dependency management practices

- Core dependencies (`pydantic`, `structlog`, `pyyaml`) use compatible-release pins (`>=x.y,<next-major`) to balance stability and security updates.
- Optional extras follow the same pinning strategy.
- `uv.lock` is committed to the repository for reproducible builds.
- Dependabot or equivalent automated scanning is recommended for forks and downstream consumers.
