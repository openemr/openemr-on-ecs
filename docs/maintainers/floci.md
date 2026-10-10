# Floci-backed live E2E emulation

This guide is for maintainers working on the live E2E tooling. It explains how
the repository uses [Floci](https://github.com/floci-io/floci), a local AWS
emulator that runs in Docker, to test the live E2E AWS adapter and runner
without touching a real AWS account.

> [!IMPORTANT]
> Floci is **not** a substitute for an approved live AWS deployment. Real
> `preflight`, `run`, and `cleanup` against AWS remain local-only and require
> the guards in [Live E2E](live-e2e.md).

## Contents

- [What CI covers](#what-ci-covers)
- [Local commands](#local-commands)
- [Safety](#safety)
- [Docker registry over HTTP](#docker-registry-over-http)
- [Runtime images](#runtime-images)
- [Limits](#limits)

## What CI covers

The **Floci Live E2E Emulation** job in
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) (see
[CI and automation](ci.md#floci-live-e2e-emulation-floci-e2e)):

1. Starts the digest-pinned `floci/floci:1.6.0` image through Testcontainers.
2. Seeds an STS identity, a dedicated Route 53 zone, the CDK bootstrap stack,
   and bootstrap IAM roles.
3. Runs `LiveE2EAws.preflight` against the emulator. Service Quotas, IAM policy
   simulation, and read APIs the emulator doesn't support (such as EFS) are
   replaced by Floci-specific shims.
4. Verifies that owned-stack cleanup refuses foreign ownership markers.
5. Runs a mocked runner path for ownership and cleanup orchestration, with CDK
   stubbed out.
6. Runs a real pinned-CDK smoke lifecycle against Floci (`tools/floci_cdk`):
   `cdk bootstrap`, then `cdk deploy`, then `cdk destroy`.
7. Runs the full guarded live E2E runner against Floci: real bootstrap, OpenEMR
   E2E stack synthesis, diff, and deploy through the pinned CDK CLI, emulated
   post-deploy validation, and owned-stack destroy.

GitHub Actions never invokes `python -m tools.live_e2e preflight|run|cleanup`
as a workflow step; the Floci job drives the same runner through pytest. The
job rejects ambient AWS credentials and doesn't use the AWS credentials action.

## Local commands

You need Docker running. Install the dependencies and the pinned CDK, then run
the Floci tests:

```bash
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
npm ci
pytest tests/tools/test_floci_emulator.py -q
pytest \
  tests/tools/test_floci_e2e.py \
  tests/tools/test_floci_cdk_deploy.py \
  tests/tools/test_floci_live_e2e_full.py \
  -m floci -q
```

CI runs these test files with `OPENEMR_FLOCI_E2E=1` set; export it locally to
match. Tests that can't start Docker or the Floci container are skipped. These
runs are outside the normal suite (`-m "not integration and not floci"`), so
leave off `--cov`; see [Tests and coverage](README.md#tests-and-coverage).

An optional Compose helper is available, but it's usually unnecessary because
the tests start Floci themselves:

```bash
docker compose -f compose/docker-compose.floci.yml up
export OPENEMR_FLOCI_E2E=1
export OPENEMR_AWS_ENDPOINT_URL=http://127.0.0.1:4566
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
```

## Safety

- `OPENEMR_FLOCI_E2E=1` is required before CI signals are ignored.
- `live_e2e_emulated=true` changes a production CDK branch only when that flag
  and a local emulator endpoint are also present in the synthesis environment.
- The endpoint hostname must be explicitly local (`localhost`, `127.0.0.1`,
  `::1`, `host.docker.internal`, `floci`, or a `.localhost` subdomain).
  Arbitrary private-network (RFC 1918) and `.local` hosts are rejected.
- Real AWS hostnames are rejected.
- Emulated mode never contacts Service Quotas. Write-permission simulation is
  replaced by IAM role existence checks after a successful `AssumeRole`.
- Emulator `UnknownOperationException` responses on read probes (for example
  EFS) are recorded as Floci-emulated passes instead of failing preflight.

## Docker registry over HTTP

Floci's emulated ECR listens on plain HTTP at
`<account>.dkr.ecr.<region>.localhost:5100`. The Floci CI job merges that host
into Docker's `insecure-registries` setting and restarts the daemon before
asset publication. Without that, `cdk-assets` or `docker push` fails with
`http: server gave HTTP response to HTTPS client`.

Locally, add the same entry to `/etc/docker/daemon.json` (or Docker Desktop's
insecure-registries setting) before running the full Floci live E2E suite.

## Runtime images

Floci starts real MySQL and Valkey containers through the host Docker socket.
For Aurora MySQL it derives the image tag from `EngineVersion`, so
`8.0.mysql_aurora.3.12.0` becomes `mysql:8.0.mysql_aurora.3.12.0`. That tag
isn't published on Docker Hub.

Before the full live E2E path runs, `tools.live_e2e.floci_images` reads
`StackConstants.AURORA_MYSQL_ENGINE_VERSION`, pulls digest-pinned `mysql:8.0`
and `valkey/valkey:8`, then retags MySQL to the exact Aurora engine tag Floci
will request. CI also runs that preparation step explicitly so the host daemon
already has the images when Floci starts its sidecar containers. The immutable
digests are declared in `tools/live_e2e/floci_images.py`; the Floci digest is
shared by Compose, the tests, and the workflow.

## Limits

When `OPENEMR_FLOCI_E2E=1` is set, the runner synthesizes with
`live_e2e_emulated=true` and applies Floci-only stack shims:

- Skip AWS Backup (Floci lacks the AWS Backup managed IAM policies, and CDK
  backup selections always attach those ARNs).
- Disable the S3 `AutoDeleteObjects` custom resources (Floci Lambda networking
  gaps).
- Skip the `OneTimeSSLSetup` CDK `TriggerFunction` (its HTTPS custom-resource
  provider fails against Floci's HTTP endpoint with TLS `EPROTO`).

Post-deploy validation is Floci-aware: it still requires CloudFormation
`CREATE_COMPLETE`, ownership markers, and the expected resource types, but it
doesn't require a publicly reachable OpenEMR HTTPS endpoint. An explicitly
approved [live AWS run](live-e2e.md) remains the standard for production-like
HTTPS, ECS health, and quota behavior.
