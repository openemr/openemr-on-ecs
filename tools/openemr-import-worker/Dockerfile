FROM python:3.14-alpine@sha256:a1321512d6a287428c50dcdf2ab3857761127e03a23b1f648e9c1c0de59288f8 AS base

# The Alpine release branch is fixed by the digest-pinned base image above.
# Do not add exact "package=version" pins here: Alpine mirrors serve only the
# current build of each package for a branch, so an exact pin turns every
# upstream security or bugfix rebuild into a build failure without making the
# image any more reproducible. tests/tools/test_openemr_import_worker.py
# enforces this.
#
# The RDS CA bundle is likewise not checksum-pinned: AWS republishes it in
# place whenever a Region is added. Its provenance is the HTTPS connection to
# AWS's trust store, so curl must refuse plain HTTP, including on redirects.
RUN apk add --no-cache \
      ca-certificates \
      curl \
      mariadb-client \
    && curl --fail --silent --show-error --location \
      --proto =https --proto-redir =https --tlsv1.2 \
      --output /etc/ssl/certs/aws-rds-global.pem \
      https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
    && adduser -D -u 1000 importer \
    && mkdir -p /work /fixtures \
    && chown importer:importer /work /fixtures

WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes --requirement requirements.txt

COPY worker.py fresh-seed-manifest.json ./

USER importer
ENTRYPOINT ["python", "/app/worker.py"]

FROM base AS ci
USER root
COPY ci_prepare_mysql_fixtures.py ci_live_mysql_import.py ci_update_seed_manifest.py .
USER importer

FROM base AS production
