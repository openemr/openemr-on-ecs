# Live E2E deployment timing

This report is generated from sanitized records produced by the guarded local live-E2E runner.
Durations are measurements, not estimates. Account IDs, resource ARNs, hostnames, and secrets are excluded.

## Summary

- Recorded runs: 2
- Successful runs: 2
- Failed or interrupted runs: 0
- Latest recorded run: 2026-11-02T12:36:39Z (`1a473a1d8060`)

## Latest successful measurement

- Run: `e2e-20261102t123544z-6f4296b7` at 2026-11-02T12:36:39Z
- Source: `Jmevorach/openemr-on-ecs` branch `main` commit `1a473a1d8060622193a164efd6476f2bcad6caea`
- Configuration: `default` in `us-east-1` (`sha256:73c9cdeaa5682817`)
- Safe stack identifier: `sha256:3ff0a9824fb7`; bootstrap: `ready-v30`
- Total E2E time: 67m 04s
- Total deployment time (including assets): 26m 58s
- ECS service creation time: 4m 23s
- Time to application readiness: 26m 29s
- Cleanup time: 40m 01s (stack-deleted-with-expected-residuals)
- Versions: Python `3.14.6`, Node.js `v24.19.0`, CDK CLI `2.1147.0 (build 21b5c6f)`, CDK library `2.275.0`, CDK assets `4.8.0`, OpenEMR `8.5.0`, Aurora `8.0.mysql_aurora.3.12.0`, runner `1.0.0`
- Residual resources: 10

## Profile statistics

Successful end-to-end totals are aggregated only within the same configuration profile.

| Profile | Successful | Failed/interrupted | Minimum | Maximum | Median | Recent successful trend |
|---|---:|---:|---:|---:|---:|---|
| default | 2 | 0 | 67m 04s | 68m 05s | 67m 34s | 68m 05s → 67m 04s |

## Historical runs

| Run | Started (UTC) | Commit | Region | Profile | Result | Total | Deploy incl. assets | ECS steady | HTTPS ready | Cleanup time | Cleanup result | Residuals |
|---|---|---|---|---|---|---:|---:|---:|---:|---:|---|---:|
| e2e-20261102t123544z-6f4296b7 | 2026-11-02T12:36:39Z | `1a473a1d8060` | us-east-1 | default | passed | 67m 04s | 26m 58s | 4m 23s | 26m 29s | 40m 01s | stack-deleted-with-expected-residuals | 10 |
| e2e-20261003t142832z-b00799f8 | 2026-10-03T14:29:54Z | `2422e248bf93` | us-east-1 | default | passed | 68m 05s | 26m 02s | 3m 23s | 25m 28s | 41m 57s | stack-deleted-with-expected-residuals | 10 |

## Methodology

- A transparent Docker proxy records monotonic build durations without recording Docker arguments;
  asset publication is the serialized CDK asset-pipeline duration minus measured Docker builds.
- CDK deployment, post-deploy HTTPS wait, cleanup, and total durations use a local monotonic clock.
- Time to application readiness spans the CloudFormation stack creation timestamp through the first
  successful local HTTPS probe.
- CloudFormation stack, Aurora, ElastiCache, EFS, and ECS durations come from AWS API event timestamps.
- HTTPS readiness requires a successful TLS request with an HTTP 200 response containing OpenEMR.
- Deployment validation also checks expected stack resources, ECS desired count, target health, Aurora,
  and both EFS file systems.
- Cleanup timing includes stack deletion, owned orphan Lambda log-group cleanup, retained-asset
  inventory, and residual-resource inventory.

## Comparability caveats

Compare runs only when profile, Region, versions, and configuration fingerprint are compatible.
Bootstrap asset caching, AWS control-plane load, DNS and certificate propagation, account quota usage,
container-image caching, and file-asset packaging can materially affect durations. Failed and interrupted runs are not
included in successful timing aggregates.

## Source and reproduction

- Machine-readable history: `e2e-results/history.json`
- Runner guide: `docs/maintainers/live-e2e.md`
- Regenerate this report without contacting AWS:

```bash
python -m tools.live_e2e report
```
