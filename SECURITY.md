# Security policy

## Supported versions

`julesctl` is pre-release software built against the Jules `v1alpha` API. Only the current `main` branch and the latest published alpha are supported.

## Reporting

Use GitHub Security Advisories for private reports. Include the affected command and version, the smallest reproducible input, the observed and expected receipts, and whether any credential or private task data may have been exposed. Do not publish sensitive values.

## Credential boundary

- `JULES_API_KEY` is accepted only from the process environment.
- Keys must not be passed on argv, committed, or stored in repository-local configuration.
- Candidate producers should use the keyless queue. Only the credentialed worker should receive the key.
- Production credentials must use the official HTTPS Jules endpoint.

## Destructive operations

Fleet deletion is preview-first. Apply only an exact stored plan and preserve its operation receipt. Pause other Jules ingress paths before an emergency drain, including native schedules, issue-label triggers, and unrelated clients.
