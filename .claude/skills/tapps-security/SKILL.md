---
name: tapps-security
user-invocable: true
model: claude-sonnet-4-6
description: >-
  Run a comprehensive security audit including vulnerability scanning
  and dependency CVE checks.
allowed-tools: >-
  mcp__tapps-mcp__tapps_security_scan
  mcp__tapps-mcp__tapps_dependency_scan
argument-hint: "[file-path]"
---

Run a comprehensive security audit using TappsMCP:

1. Call `mcp__tapps-mcp__tapps_security_scan` on the target file to detect vulnerabilities
2. Call `mcp__tapps-mcp__tapps_dependency_scan` to check for known CVEs in dependencies
3. Group all findings by severity (critical, high, medium, low)
4. Suggest a prioritized fix order starting with the highest-severity issues
