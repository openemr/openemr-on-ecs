# Local testing with Docker Compose

This guide shows you how to run the OpenEMR container on your own computer, with the same startup
script the stack uses in AWS, so you can test changes without deploying anything. It's for
developers changing the container startup logic in `openemr_ecs/compute.py` or checking a new
OpenEMR version.

> [!NOTE]
> This is only for testing. The settings, passwords, and self-signed certificates used here must
> never be used in production, and all test data is deleted when you tear the containers down.

## Before you begin

- [Docker](https://docs.docker.com/get-docker/) with Docker Compose (either `docker compose` or the
  older `docker-compose` works).
- Several GB of free disk space for the images.
- Internet access: the startup script downloads the Amazon Root CA and the RDS CA bundle, just as it
  does in AWS.
- The containers use the ARM64 image (`platform: linux/arm64`). On Intel or AMD computers, Docker
  runs it under emulation, which is slower.
- **Cost:** none; nothing runs in AWS.
- **Time:** OpenEMR takes about 2–5 minutes to initialize on first start.

## What this tests

The Compose files run the official OpenEMR image, pinned to the same tag and reviewed ARM64 digest
as the stack (`openemr/openemr:8.4.1@sha256:22bc7022e0fb3f88909d87a428849038a722a736634c1dbbae2f07e92ab362ad`),
with the same startup command the stack uses on ECS. That startup command:

1. **Makes sure the site structure exists.** If `sites/default` is missing (as on a brand-new EFS
   volume), it restores it from the image.
2. **Creates the certificate and document folders** OpenEMR needs.
3. **Downloads certificates** with retries: the Amazon Root CA 1 (for Valkey/Redis TLS) and the RDS
   CA bundle (for MySQL TLS).
4. **Sets ownership and permissions** on the certificate files.
5. **Waits for the database and cache** to be reachable.
6. **Runs `openemr.sh`**, which performs OpenEMR's automated setup or upgrade.

Compared with ECS, the local setup uses MariaDB 11.8 instead of Aurora, Docker volumes instead of
EFS, plain environment variables instead of Secrets Manager, and no load balancer.

## Option 1: Basic test (no TLS)

This is the quickest check of the startup logic.

1. From the repository root, run:

   ```bash
   ./scripts/test-startup.sh
   ```

   The script removes any previous test containers and volumes (`down -v`), starts the database,
   waits a few seconds, then builds and starts OpenEMR and shows its logs. It starts three
   containers: `openemr-container-test` (OpenEMR), `mysql-test` (MariaDB), and `redis-test`
   (Redis 7).

   Or start it by hand:

   ```bash
   docker compose -f compose/docker-compose.test.yml up
   ```

2. Watch the logs. Success looks like the startup script's log lines finishing without `ERROR`,
   followed by OpenEMR's own setup messages.

## Option 2: TLS test (closer to production)

This turns on TLS for both the database and the cache, as in AWS, where Aurora requires encrypted
connections (`require_secure_transport=ON`) and Valkey uses TLS.

1. Run:

   ```bash
   ./scripts/test-startup-ssl.sh
   ```

   Or by hand:

   ```bash
   docker compose -f compose/docker-compose.test-ssl.yml up
   ```

The TLS setup:

- starts MariaDB (`mysql-test-ssl`) with a wrapper entrypoint
  ([`scripts/mysql-entrypoint-wrapper.sh`](../../scripts/mysql-entrypoint-wrapper.sh)) that
  generates a test certificate authority and server certificates, and runs with
  `--require-secure-transport=ON`;
- starts Redis (`redis-test-ssl`) with TLS on port 6380 and its own generated certificate authority;
- configures OpenEMR (`openemr-container-test-ssl`) with `MYSQL_SSL=ON` and `REDIS_TLS=ON`, using
  the test certificate authorities copied from the database and cache containers.

The script starts the database first, waits for it to answer and checks that its certificates
exist, then starts OpenEMR.

## Check that OpenEMR is running

The test containers don't publish any ports on your computer, so check from inside the OpenEMR
container:

```bash
docker compose -f compose/docker-compose.test.yml exec openemr-test \
  curl -s -o /dev/null -w "%{http_code}\n" http://localhost/
```

A `200` or `302` means OpenEMR is answering. For the TLS test, use
`-f compose/docker-compose.test-ssl.yml exec openemr-test-ssl` instead.

To look around inside the container:

```bash
docker compose -f compose/docker-compose.test.yml exec openemr-test sh

# Does the site folder exist?
ls -la /var/www/localhost/htdocs/openemr/sites/default/

# Does sqlconf.php exist?
ls -la /var/www/localhost/htdocs/openemr/sites/default/sqlconf.php

# Are the certificate files in place?
ls -la /var/www/localhost/htdocs/openemr/sites/default/documents/certificates/
ls -la /root/certs/mysql/server/
ls -la /root/certs/redis/
```

## View logs and stop

Use the Compose file that matches the test you started:

```bash
# Basic test
docker compose -f compose/docker-compose.test.yml logs -f openemr-test
docker compose -f compose/docker-compose.test.yml down -v

# TLS test
docker compose -f compose/docker-compose.test-ssl.yml logs -f openemr-test-ssl
docker compose -f compose/docker-compose.test-ssl.yml down -v
```

`down -v` also deletes the test volumes, so the next run starts fresh. Leave off `-v` to keep the
data.

## Customize the test

You can override the test's settings with environment variables before running the script:

```bash
export MYSQL_HOST=mysql-test
export MYSQL_ROOT_PASS=testpass
export MYSQL_USER=openemr
export MYSQL_PASS=openemr
export MYSQL_DATABASE=openemr
export SWARM_MODE=yes
export AUTHORITY=yes
export OE_USER=admin
export OE_PASS=pass

./scripts/test-startup.sh
```

The values above are the defaults. Two that are especially useful:

- **`MYSQL_DATABASE`**: use a unique name (for example `openemr_v2`) to avoid clashing with data
  from earlier runs. In AWS, the database name is always `openemr`.
- **`SWARM_MODE` and `AUTHORITY`**: `yes` makes this container the "leader" that initializes
  OpenEMR, as the first task does on ECS.

To test changes to OpenEMR's own `openemr.sh`, clone
[`openemr/openemr`](https://github.com/openemr/openemr), and uncomment the `volumes` example in
`compose/docker-compose.test.yml` that mounts `docker/release/openemr.sh` into the container. Check
out the tag matching the image version. (The comments in the Compose file and older docs mention
`v8_2_0`, but the image is now 8.4.1.)

## How leadership works

On ECS, several OpenEMR tasks can start at the same time. OpenEMR's `openemr.sh` decides which one
does the first-time setup using two files on the shared storage: `sites/docker-leader` and
`sites/docker-completed`.

If the leader fails before finishing, it can leave a stale `docker-leader` file behind. The stack's
startup script warns about a `docker-leader` file older than 20 minutes. The real fix is making sure
the leader succeeds, with correct TLS files and settings. To force a reset while testing, delete
those two files from the shared storage (a Docker volume locally, or the sites EFS in AWS).

## If something goes wrong

| Problem | What to do |
|---|---|
| `PHP Warning: require_once(.../sites/default/sqlconf.php): Failed to open stream` | The site folder wasn't initialized. `openemr.sh` restores it from `/swarm-pieces/sites` when `SWARM_MODE=yes` and `AUTHORITY=yes`; make sure both are set. |
| OpenEMR can't connect to the database | Check the database container is running (`docker compose -f compose/docker-compose.test.yml ps`), read its logs (`... logs mysql-test`), and make sure the `MYSQL_*` variables match between the containers. |
| Certificate download fails | Check internet access from the container: `docker compose -f compose/docker-compose.test.yml exec openemr-test curl -I https://www.amazontrust.com/repository/AmazonRootCA1.pem` |
| TLS errors in the TLS test | Wait for the database to finish starting (check its health), confirm its certificates exist with `docker exec mysql-test-ssl ls -la /etc/mysql/ssl/`, and check the CA paths. |
| Container exits immediately | Read `docker compose ... logs`, make sure Docker has enough CPU, memory, and disk, and that the Docker daemon is running (`docker ps`). |
| OpenEMR never answers | Give it 2–5 minutes on first start, then look for errors in the logs. |

## Next steps

After your change works locally:

1. Update `openemr_ecs/compute.py` if needed (the Compose files copy its startup command, so keep
   them in sync).
2. Test locally again.
3. Deploy to a test stack with `node_modules/.bin/cdk deploy`.

## Related

- [`compose/README.md`](../../compose/README.md) and [`scripts/README.md`](../../scripts/README.md)
- [Floci emulator tests](../maintainers/floci.md) (the `docker-compose.floci.yml` file)
- [CI](../maintainers/ci.md): how these tests run in GitHub Actions
- [Troubleshooting](../reference/troubleshooting.md)
- [Architecture](../reference/architecture.md)
