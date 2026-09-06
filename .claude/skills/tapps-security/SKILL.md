---
name: tapps-security
user-invocable: true
model: claude-sonnet-5
description: >-
  Run a comprehensive security audit including vulnerability scanning
  and dependency CVE checks. Use when reviewing security-sensitive changes,
  before a security audit, or before a production release.
allowed-tools: >-
  mcp__nlt-build__tapps_security_scan
  mcp__nlt-build__tapps_dependency_scan
argument-hint: "[file-path]"
---
<!-- BEGIN: tapps-skill tapps-security v3.12.83 -->
<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->

Run a comprehensive security audit using TappsMCP:

1. Call `mcp__nlt-build__tapps_security_scan` on the target file to detect vulnerabilities
2. Call `mcp__nlt-build__tapps_dependency_scan` to check for known CVEs in dependencies
3. Group all findings by severity (critical, high, medium, low)
4. Suggest a prioritized fix order starting with the highest-severity issues
<!-- END: tapps-skill -->

<!-- tapps-skill-project-customizations: preserved from the pre-marker version — review and trim any content the managed block above now covers -->
<!-- flagged: 100% of this region's lines duplicate the managed block above — review and trim -->

<!-- upgrade-policy: overwrite. tapps_upgrade replaces this file wholesale on every run and local edits are lost (tapps_init leaves an existing copy alone; upgrade does not). Fold the change upstream into the platform template, or pin the whole directory with an upgrade_skip_files token. -->

Run a comprehensive security audit using TappsMCP:

1. Call `mcp__nlt-build__tapps_security_scan` on the target file to detect vulnerabilities
2. Call `mcp__nlt-build__tapps_dependency_scan` to check for known CVEs in dependencies
3. Group all findings by severity (critical, high, medium, low)
4. Suggest a prioritized fix order starting with the highest-severity issues
