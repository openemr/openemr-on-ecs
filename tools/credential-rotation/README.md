# OpenEMR Credential Rotation Task

Dual-slot credential rotation for OpenEMR on ECS + EFS.

**Python version**: Controlled by `openemr_ecs.constants.StackConstants.CREDENTIAL_ROTATION_PYTHON_VERSION`. The monthly version check workflow tracks this and reports when a newer `python:X-slim` image is available.

## Table of Contents

- [Runtime assumptions](#runtime-assumptions)
- [CLI](#cli)
- [Required environment variables](#required-environment-variables)
- [Rotation algorithm](#rotation-algorithm)

## Runtime assumptions

- Database configuration is read from `sites/default/sqlconf.php` on the shared `sites` volume.
- Runtime refresh uses a forced ECS rolling deployment for zero-downtime config pickup.

## CLI

```bash
python -m credential_rotation.cli --log-json
```

Flags:
- `--dry-run`
- `--log-json`
- `--sync-db-users`
- `--fix-permissions`

## Required environment variables

- `AWS_REGION`
- `RDS_SLOT_SECRET_ID`
- `RDS_ADMIN_SECRET_ID`
- `OPENEMR_SITES_MOUNT_ROOT`
- `OPENEMR_ECS_CLUSTER`
- `OPENEMR_ECS_SERVICE`
- `OPENEMR_HEALTHCHECK_URL` (optional)

## Rotation algorithm

1. Determine active slot (`A` or `B`)
2. Select standby slot
3. Validate admin credentials (auto-heals drift)
4. Flip EFS `sqlconf.php` to standby DB credentials
5. Force rolling ECS deployment
6. Validate DB + app health
7. Rotate old slot credentials
8. Validate old slot independently
9. Finalize secret state (`active_slot` points to current)
10. Rotate admin password

Rollback:
- If flip validation fails, revert `sqlconf.php`, refresh ECS again, re-validate recovery, and fail.
- If old-slot rotation fails, keep the app on current active slot and fail.
