# Story 67.4 -- TLS documentation and nginx SSL config for the visual endpoint

<!-- docsmcp:start:user-story -->

> **As a** operator exposing the brain-visual dashboard to a team or production network, **I want** a documented and tested path to add TLS to the visual endpoint, **so that** traffic between the browser and the nginx container is encrypted and the deployment meets basic security requirements

<!-- docsmcp:end:user-story -->

<!-- docsmcp:start:sizing -->
**Points:** 3 | **Size:** M

<!-- docsmcp:end:sizing -->

<!-- docsmcp:start:purpose-intent -->
## Purpose & Intent

This story exists so that the acceptance criteria below are met and the feature is delivered. Refine this paragraph to state why this story exists and what it enables.

<!-- docsmcp:end:purpose-intent -->

<!-- docsmcp:start:description -->
## Description

nginx-visual.conf only listens on port 80. There is no TLS anywhere in the hive stack. This story adds a docker/nginx-visual-tls.conf example that adds an SSL server block (port 443, certificate volume mounts, HSTS header, HTTP→HTTPS redirect), publishes docs/guides/hive-tls.md covering both the nginx-native path (self-signed for dev, Certbot/acme.sh for production) and a Caddy reverse-proxy overlay as a zero-config alternative, and cross-links from docker/README.md and docs/guides/hive-deployment.md.

See [Epic 67](../EPIC-067.md) for project context and shared definitions.

<!-- docsmcp:end:description -->

<!-- docsmcp:start:files -->
## Files

- `docker/nginx-visual-tls.conf`
- `docs/guides/hive-tls.md`
- `docker/README.md`
- `docs/guides/hive-deployment.md`

<!-- docsmcp:end:files -->

<!-- docsmcp:start:tasks -->
## Tasks

- [ ] Create docker/nginx-visual-tls.conf with: HTTP redirect block on port 80, HTTPS server block on port 443 with ssl_certificate and ssl_certificate_key volume paths, HSTS header, and the existing /snapshot proxy location (`docker/nginx-visual-tls.conf`)
- [ ] Create docs/guides/hive-tls.md covering: (1) self-signed cert for dev with openssl command, (2) Let's Encrypt / Certbot for production, (3) Caddy reverse-proxy overlay as alternative, (4) docker-compose snippet showing cert volume mounts (`docs/guides/hive-tls.md`)
- [ ] Cross-link hive-tls.md from docker/README.md 'Before you deploy' section (`docker/README.md`)
- [ ] Cross-link hive-tls.md from docs/guides/hive-deployment.md (`docs/guides/hive-deployment.md`)

<!-- docsmcp:end:tasks -->

<!-- docsmcp:start:acceptance-criteria -->
## Acceptance Criteria

- [ ] docker/nginx-visual-tls.conf exists and is syntactically valid (nginx -t passes)
- [ ] docs/guides/hive-tls.md exists and covers at minimum: self-signed dev path
- [ ] Let's Encrypt prod path
- [ ] Caddy alternative
- [ ] docker/README.md and docs/guides/hive-deployment.md both link to hive-tls.md
- [ ] The nginx-visual-tls.conf preserves all existing /snapshot proxy settings from nginx-visual.conf

<!-- docsmcp:end:acceptance-criteria -->

<!-- docsmcp:start:definition-of-done -->
## Definition of Done

Definition of Done per [Epic 67](../EPIC-067.md).

<!-- docsmcp:end:definition-of-done -->

<!-- docsmcp:start:test-cases -->
## Test Cases

1. `test_ac1_dockernginxvisualtlsconf_exists_syntactically_valid_nginx_t_passes` -- docker/nginx-visual-tls.conf exists and is syntactically valid (nginx -t passes)
2. `test_ac2_docsguideshivetlsmd_exists_covers_at_minimum_selfsigned_dev_path` -- docs/guides/hive-tls.md exists and covers at minimum: self-signed dev path
3. `test_ac3_lets_encrypt_prod_path` -- Let's Encrypt prod path
4. `test_ac4_caddy_alternative` -- Caddy alternative
5. `test_ac5_dockerreadmemd_docsguideshivedeploymentmd_both_link_hivetlsmd` -- docker/README.md and docs/guides/hive-deployment.md both link to hive-tls.md
6. `test_ac6_nginxvisualtlsconf_preserves_all_existing_snapshot_proxy_settings_from` -- The nginx-visual-tls.conf preserves all existing /snapshot proxy settings from nginx-visual.conf

<!-- docsmcp:end:test-cases -->

<!-- docsmcp:start:technical-notes -->
## Technical Notes

- The nginx image in Dockerfile.visual is nginx:alpine — SSL modules are included by default
- no recompile needed
- Cert volume paths in the example should use conventional /etc/nginx/certs/ and be documented as operator-managed (not baked into the image)
- Caddy is the simplest zero-config option for operators who do not want to manage certs manually — a single Caddyfile with reverse_proxy tapps-visual:80 is sufficient
- Do not change the default docker-compose.hive.yaml to use TLS — keep port 80 as the default and document TLS as an opt-in overlay

<!-- docsmcp:end:technical-notes -->

<!-- docsmcp:start:dependencies -->
## Dependencies

- List stories or external dependencies that must complete first...

<!-- docsmcp:end:dependencies -->

<!-- docsmcp:start:invest -->
## INVEST Checklist

- [x] **I**ndependent -- Can be developed and delivered independently
- [ ] **N**egotiable -- Details can be refined during implementation
- [x] **V**aluable -- Delivers value to a user or the system
- [x] **E**stimable -- Team can estimate the effort
- [x] **S**mall -- Completable within one sprint/iteration
- [x] **T**estable -- Has clear criteria to verify completion

<!-- docsmcp:end:invest -->
