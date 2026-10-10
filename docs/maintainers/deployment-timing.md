# Live E2E deployment timing

This report is generated from sanitized records produced by the guarded local live-E2E runner.
Durations are measurements, not estimates. Account IDs, resource ARNs, hostnames, and secrets are excluded.

## Summary

- Recorded runs: 1
- Successful runs: 1
- Failed or interrupted runs: 0
- Latest recorded run: 2026-10-03T14:29:54Z (`2422e248bf93`)

## Latest successful measurement

- Run: `e2e-20261003t142832z-b00799f8` at 2026-10-03T14:29:54Z
- Source: `Jmevorach/openemr-on-ecs` branch `main` commit `2422e248bf93962255228721218360d018f391ef`
- Configuration: `default` in `us-east-1` (`sha256:2db0c2e0f905342b`)
- Safe stack identifier: `sha256:772a3f4e64ee`; bootstrap: `ready-v30`
- Total E2E time: 68m 05s
- Total deployment time (including assets): 26m 02s
- ECS service creation time: 3m 23s
- Time to application readiness: 25m 28s
- Cleanup time: 41m 57s (stack-deleted-with-expected-residuals)
- Versions: Python `3.14.6`, Node.js `v24.19.0`, CDK CLI `2.1143.0 (build 9c6bd0e)`, CDK library `2.272.0`, CDK assets `4.7.3`, OpenEMR `8.4.1`, Aurora `8.0.mysql_aurora.3.12.0`, runner `1.0.0`
- Residual resources: 10

## Profile statistics

Successful end-to-end totals are aggregated only within the same configuration profile.

| Profile | Successful | Failed/interrupted | Minimum | Maximum | Median | Recent successful trend |
|---|---:|---:|---:|---:|---:|---|
| default | 1 | 0 | 68m 05s | 68m 05s | 68m 05s | 68m 05s |

## Historical runs

| Run | Started (UTC) | Commit | Region | Profile | Result | Total | Deploy incl. assets | ECS steady | HTTPS ready | Cleanup time | Cleanup result | Residuals |
|---|---|---|---|---|---|---:|---:|---:|---:|---:|---|---:|
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
