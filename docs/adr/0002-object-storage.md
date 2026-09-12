# ADR 0002: Use Garage for the Phase-1 S3-compatible service

**Status:** Accepted on 2026-09-12

## Decision

Use Garage `v2.3.0` (`dxflrs/garage`, pinned by digest in Compose) as the self-hosted S3-compatible byte store. Use `boto3==1.43.93` only inside the `S3ObjectStore` adapter. PostgreSQL remains authoritative for artifact IDs, ownership, checksums, retention, privacy, provenance, and lifecycle state.

## Evidence and fit

Garage is an actively maintained, self-hosted S3-compatible object service with documented fixed-tag Docker deployment. It is AGPL-3.0, so this project runs its unmodified container as an external service and documents the source/upgrade obligation for any network deployment. Its client HTTP API needs a private network in development and TLS termination in production.

## Rejected candidate

SeaweedFS was evaluated first (Apache-2.0 and active), then rejected as the Phase-1 default: GHSA-99q7-x53r-6j4g is a high-severity, unpatched cross-prefix write issue affecting recent versions. RustFS was also rejected because its current advisory page contains several high/critical IAM and service-account issues. Neither risk profile is appropriate as the default foundation.

## Security and operations

Garage has a disclosed medium upstream dependency issue; Phase 1 does not enable its metrics/Prometheus surface, exposes no storage port to the host, uses a single internal service credential, and treats the stack as a development deployment only. Production deployment requires a fresh advisory review, TLS reverse proxy, disk encryption policy, credential rotation, and a decision on AGPL network-use obligations.

## Exit path

The `ObjectStore` contract and S3-compatible API allow Garage to be replaced by a patched self-hosted store, Ceph RGW, or managed S3. Export is a bucket copy plus PostgreSQL artifact metadata export; no Garage IDs are canonical.
