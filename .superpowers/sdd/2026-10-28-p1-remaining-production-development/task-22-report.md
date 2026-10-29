# Task 22 exact candidate build report

Date: 2026-10-29

## Result and Git identity

The development-only v2 publication-worker launch lock was regenerated from frozen source commit `db3613482ac78cd708f6aa62e64854626ba7a906`, source tree `f6ed4a172065985dd13b92994cb781318e0960f9`. The artifact commit is `c1c78dd25e467d0ae48e9c949de6aeb555875ea8`, tree `7212ba6537bcdde0196c55152ac0814c59787073`; its sole parent is the exact source commit. It changes only:

- `deployment/v4/publication-worker-launch-lock-v2-development.json`
- `deployment/v4/publication-worker-launch-lock-v2-development.sha256`

`git diff --check db3613482ac78cd708f6aa62e64854626ba7a906 c1c78dd25e467d0ae48e9c949de6aeb555875ea8` passed. The worktree is clean. The v1 lock was byte-compared with the frozen source and is unchanged. No push or merge occurred.

## Reproducibility and artifact digests

- Source commit/tree: `db3613482ac78cd708f6aa62e64854626ba7a906` / `f6ed4a172065985dd13b92994cb781318e0960f9`.
- Artifact commit/tree/parent: `c1c78dd25e467d0ae48e9c949de6aeb555875ea8` / `7212ba6537bcdde0196c55152ac0814c59787073` / `db3613482ac78cd708f6aa62e64854626ba7a906`.
- Source timestamp used for `SOURCE_DATE_EPOCH`: `1793263327`.
- Independent `git archive` exports had 901 files each and compared equal with `diff -qr`. The source archive SHA-256 was `a54fe4c5c411487c35d173d2b6b653d4f4d42083a3f497a17f59a5e0a7a10c24`.
- Both isolated PEP 517 wheels were 459,013 bytes and had SHA-256 `7e3073d66ffc6f6d0eb9aa4a27e08e506d82b7ecf58d749558cd6544d805a5b6`; `cmp` passed.
- Locally built development image digest: `sha256:24084cf14fadcb711bccefb9ab24c72444c0740ad819d79a3756831b2e008fd6`. Build metadata records the unchanged Python base image digest `sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419`.
- Extracted image interpreter SHA-256: `73b68fc2dac176af16098180a2c98a03ee9f1f1b02db25d42d44d782e79e2330`.
- Extracted runtime package inventory: 73 distributions, SHA-256 `349782ff04770a3543668323fd5bcd88783e5b2b34fb90ce6a7cf1f3f2867c06`, retained at `/tmp/salience-p1-task22-final-20261029-SI95HA/resolved-runtime-packages.txt`.
- Launch-lock source manifest SHA-256: `b4fbe57aacde546f7733f839211cf211d7a4d12ac35cc901f2c74f90e2c33fe8`; both successful builder runs printed this digest.
- v2 lock JSON SHA-256 and companion content: `d7cc873fe32beffcf042db7133b7b134be23d6ad9774b75568f337482a4e7bc1`.
- v2 companion file SHA-256: `7d30bce4d395dcd3242dff345ecc88f4fe45e2485e8ae6dba43c418d61d6420f`.
- Preserved v1 lock SHA-256: `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c`; frozen-source byte comparison passed.
- Synthetic route-bundle file SHA-256: `934d70443c41ea81f46be6638d39e51a2ca1b55419e6f4fa3c6bf278660165ec`.

## Synthetic route and trust state

The single Task 22 route binds the exact wheel and local image digests above:

- Deployment UUID: `00000000-0000-0000-0000-000000000221`.
- Environment UUID: `00000000-0000-0000-0000-000000000222`.
- Route UUID: `00000000-0000-0000-0000-000000000223`.
- Slot UUID: `00000000-0000-0000-0000-000000000224`.
- Build ID: `p1-task22-development-only`.
- Route tuple SHA-256: `6f56661b24a5032deb6f5bfea8459b17d4efdbdee03d6131ac8ff441f47cd25c`.
- Route manifest SHA-256: `baf5884f361937459d47e3d6467565acc54d2f6a28d0584978556b60e37935dc`.

The route has workflow `GovernedPublicationWorkflow` and the exact nine existing `salience.publication.*` activities: `authorize`, `await`, `cancel`, `complete`, `dead_letter`, `delivery`, `denied`, `request`, and `submit_or_reconcile`. Producer bindings are empty. Its Temporal endpoint identifier is `development-temporal.unprovisioned`, compatibility is `exact-head`, qualification is `development-only`, migration head is `0051_p1_recovery_rollback_v2`, and rollback verifier ABI is `salience.p1-recovery-rollback-verifier.v2`. No external production trust digest is provisioned; this artifact does not qualify production or enable production effects.

## Build commands and results

All build files, source exports, logs, generated route/locks, extracted runtime, metadata, package inventory, and verification output are retained under `/tmp/salience-p1-task22-final-20261029-SI95HA`.

The two independent clean exports were made with:

```bash
git archive --format=tar db3613482ac78cd708f6aa62e64854626ba7a906 | tar -xf - -C /tmp/salience-p1-task22-final-20261029-SI95HA/source-1
git archive --format=tar db3613482ac78cd708f6aa62e64854626ba7a906 | tar -xf - -C /tmp/salience-p1-task22-final-20261029-SI95HA/source-2
diff -qr /tmp/salience-p1-task22-final-20261029-SI95HA/source-1 /tmp/salience-p1-task22-final-20261029-SI95HA/source-2
```

The first no-index PEP 517 attempt exited 1 because the local cache did not contain `setuptools>=80.9.0`. Under the previously authorized build-infrastructure boundary, each wheel was then built from its separate export with normal isolated PEP 517 requirement retrieval:

```bash
SOURCE_DATE_EPOCH=1793263327 PIP_DISABLE_PIP_VERSION_CHECK=1 \
  /home/admin/Salience/.venv/bin/python -m pip wheel --no-deps \
  --wheel-dir /tmp/salience-p1-task22-final-20261029-SI95HA/wheels-1 \
  /tmp/salience-p1-task22-final-20261029-SI95HA/source-1

SOURCE_DATE_EPOCH=1793263327 PIP_DISABLE_PIP_VERSION_CHECK=1 \
  /home/admin/Salience/.venv/bin/python -m pip wheel --no-deps \
  --wheel-dir /tmp/salience-p1-task22-final-20261029-SI95HA/wheels-2 \
  /tmp/salience-p1-task22-final-20261029-SI95HA/source-2
```

Both exited 0; wheel bytes matched. The local image context was assembled only from the export’s unchanged `.dockerignore`, `Dockerfile`, `alembic.ini`, and `migrations`, plus wheel 1 as `salience.whl`.

The first local Docker build used `--network=none` and exited 1 because the unchanged Dockerfile could not retrieve its pinned runtime dependency `alembic==1.20.0`. The normal local build was then run without `--push`; it exited 0:

```bash
docker --context default buildx build --load --progress=plain \
  --metadata-file /tmp/salience-p1-task22-final-20261029-SI95HA/image-metadata.json \
  --tag salience-task22-development-p1-20261029-si95ha:latest \
  --build-arg SALIENCE_WHEEL_FILE=salience.whl \
  /tmp/salience-p1-task22-final-20261029-SI95HA/build-context
```

The configured Python package index was used only by the isolated wheel build and unchanged Dockerfile dependency installation. No application provider, production system, live schedule, alert endpoint, or external acceptance service was contacted. The full 73-package image inventory and exact versions are retained in `resolved-runtime-packages.txt` and hashed above.

The runtime extraction used the exact local image and a named, disposable container:

```bash
docker --context default create --name salience-task22-runtime-extract-20261029-si95ha salience-task22-development-p1-20261029-si95ha:latest
docker --context default cp salience-task22-runtime-extract-20261029-si95ha:/usr/local/lib/python3.13/site-packages /tmp/salience-p1-task22-final-20261029-SI95HA/runtime-stage/usr/local/lib/python3.13/site-packages
docker --context default cp salience-task22-runtime-extract-20261029-si95ha:/usr/local/bin/python3.13 /tmp/salience-p1-task22-final-20261029-SI95HA/extracted/image-interpreter
docker --context default cp salience-task22-runtime-extract-20261029-si95ha:/usr/local/bin/content /tmp/salience-p1-task22-final-20261029-SI95HA/runtime-stage/usr/local/bin/content
```

An initial pair of lock-builder attempts failed before output because the extracted runtime omitted `/usr/local/bin/content`, which the installed wheel's `RECORD` names. Copying that file from the same exact image resolved the cause; the two successful independent builder invocations were:

```bash
for index in 1 2; do
  env -i PATH="$PATH" HOME=/home/admin PYTHONPATH=/home/admin/Salience/src \
    /home/admin/Salience/.venv/bin/python \
    /home/admin/Salience/scripts/build_publication_worker_launch_lock.py \
    --application-commit db3613482ac78cd708f6aa62e64854626ba7a906 \
    --wheel "/tmp/salience-p1-task22-final-20261029-SI95HA/wheels-$index/salience-0.1.0-py3-none-any.whl" \
    --runtime-root /tmp/salience-p1-task22-final-20261029-SI95HA/runtime-stage/usr/local/lib/python3.13/site-packages \
    --lock "/tmp/salience-p1-task22-final-20261029-SI95HA/locks/lock-$index.json" \
    --independent-digest "/tmp/salience-p1-task22-final-20261029-SI95HA/locks/lock-$index.sha256" \
    --control-dir "/tmp/salience-p1-task22-final-20261029-SI95HA/locks/control-$index" \
    --interpreter-source /tmp/salience-p1-task22-final-20261029-SI95HA/extracted/image-interpreter \
    --route-bundle /tmp/salience-p1-task22-final-20261029-SI95HA/route-bundle.json
done
```

Both exited 0 and emitted the same source-manifest digest. `cmp` passed for lock JSON, companion, generated control lock, control digest, and launcher outputs. The final lock and companion were checked against the expected Task 22 route IDs, wheel/image hashes, v2 schema, qualification, migration head, ABI, allowlists, endpoint, and empty producer bindings. The companion is exactly the JSON SHA-256 followed by a newline.

The artifact commit command staged only the generated pair:

```bash
git add -- deployment/v4/publication-worker-launch-lock-v2-development.json \
  deployment/v4/publication-worker-launch-lock-v2-development.sha256
git commit -m "build: refresh Task22 exact development v2 lock"
```

After commit, direct-parent, exact changed-path, lock/companion, v1 preservation, wheel reproducibility, route parsing, clean-worktree, and `git diff --check` validations all passed. The focused and full application test suites were not run; this assignment was limited to building and committing the exact candidate artifact.

## Resolved image runtime package inventory

The exact installed inventory (73 distributions) is retained at `/tmp/salience-p1-task22-final-20261029-SI95HA/resolved-runtime-packages.txt`:

```text
Mako==1.4.3
MarkupSafe==3.0.4
PyJWT==2.14.0
PyYAML==6.0.3
SQLAlchemy==2.0.52
a2a-sdk==1.1.2
alembic==1.20.0
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.15.1
asyncpg==0.31.0
attrs==26.1.0
boto3==1.43.93
botocore==1.43.121
certifi==2026.10.23
cffi==2.1.1
charset-normalizer==3.5.2
click==8.5.0
cryptography==50.0.1
fastapi==0.141.1
google-api-core==2.43.1
google-auth==2.62.0
googleapis-common-protos==1.76.0
greenlet==3.5.6
h11==0.16.0
httpcore2==2.16.0
httpcore==1.0.9
httptools==0.9.0
httpx2==2.16.0
httpx==0.28.1
idna==3.20
jmespath==1.1.0
json-rpc==1.15.0
jsonschema-specifications==2025.9.1
jsonschema==4.26.0
mcp-types==2.2.0
mcp==2.2.0
nexus-rpc==1.4.0
opentelemetry-api==1.44.0
opentelemetry-sdk==1.44.0
opentelemetry-semantic-conventions==0.65b0
packaging==26.3
pip==25.1.1
proto-plus==1.29.0
protobuf==6.33.6
psycopg-binary==3.3.5
psycopg==3.3.5
pyasn1==0.6.5
pyasn1_modules==0.4.2
pycparser==3.12
pydantic==2.12.5
pydantic_core==2.41.5
python-dateutil==2.9.0.post0
python-dotenv==1.2.4
python-multipart==0.0.32
referencing==0.37.0
requests==2.34.2
rpds-py==2026.9.1
s3transfer==0.19.3
salience==0.1.0
six==1.17.0
sse-starlette==3.6.0
starlette==1.10.0
temporalio==1.32.0
truststore==0.10.5
types-protobuf==7.35.1.20260906
typing-inspection==0.4.4
typing_extensions==4.16.0
urllib3==2.8.0
uvicorn==0.40.0
uvloop==0.23.0
watchfiles==1.3.0
websockets==17.3
```

## Cleanup and limitations

The exact extraction container `salience-task22-runtime-extract-20261029-si95ha` and exact local image tag `salience-task22-development-p1-20261029-si95ha:latest` were removed and verified absent. BuildKit cache was left intact; no host-wide prune ran. Build logs and generated build evidence remain under `/tmp/salience-p1-task22-final-20261029-SI95HA`. No provider/live/production/external-acceptance contact, publish, push, or merge occurred. Production trust remains unprovisioned, qualification remains development-only, and production effects remain disabled.

## Exact command records

The temporary build root was allocated with `mktemp -d /tmp/salience-p1-task22-final-20261029-XXXXXX`; it resolved to `/tmp/salience-p1-task22-final-20261029-SI95HA`. The clean export equality check and recorded epoch were:

```bash
git archive --format=tar db3613482ac78cd708f6aa62e64854626ba7a906 | tar -xf - -C /tmp/salience-p1-task22-final-20261029-SI95HA/source-1
git archive --format=tar db3613482ac78cd708f6aa62e64854626ba7a906 | tar -xf - -C /tmp/salience-p1-task22-final-20261029-SI95HA/source-2
diff -qr /tmp/salience-p1-task22-final-20261029-SI95HA/source-1 /tmp/salience-p1-task22-final-20261029-SI95HA/source-2
git archive --format=tar db3613482ac78cd708f6aa62e64854626ba7a906 | sha256sum
git show -s --format=%ct db3613482ac78cd708f6aa62e64854626ba7a906
```

The exact offline wheel attempt was:

```bash
env -i PATH="$PATH" HOME=/home/admin PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 SOURCE_DATE_EPOCH=1793263327 \
  /home/admin/Salience/.venv/bin/python -m pip wheel --no-deps \
  --wheel-dir /tmp/salience-p1-task22-final-20261029-SI95HA/wheels-1 \
  /tmp/salience-p1-task22-final-20261029-SI95HA/source-1
```

It exited 1 because `setuptools>=80.9.0` was unavailable with index access disabled. The successful exact commands used for the two independent wheels were:

```bash
env -i PATH="$PATH" HOME=/home/admin PIP_DISABLE_PIP_VERSION_CHECK=1 SOURCE_DATE_EPOCH=1793263327 \
  /home/admin/Salience/.venv/bin/python -m pip wheel --no-deps \
  --wheel-dir /tmp/salience-p1-task22-final-20261029-SI95HA/wheels-1 \
  /tmp/salience-p1-task22-final-20261029-SI95HA/source-1

env -i PATH="$PATH" HOME=/home/admin PIP_DISABLE_PIP_VERSION_CHECK=1 SOURCE_DATE_EPOCH=1793263327 \
  /home/admin/Salience/.venv/bin/python -m pip wheel --no-deps \
  --wheel-dir /tmp/salience-p1-task22-final-20261029-SI95HA/wheels-2 \
  /tmp/salience-p1-task22-final-20261029-SI95HA/source-2
```

The exact offline image command exited 1 on pinned `alembic==1.20.0`:

```bash
env -i PATH="$PATH" HOME=/home/admin DOCKER_CONTEXT=default \
  docker --context default buildx build --load --network=none --progress=plain \
  --metadata-file /tmp/salience-p1-task22-final-20261029-SI95HA/image-metadata-offline.json \
  --tag salience-task22-development-p1-20261029-si95ha:latest \
  --build-arg SALIENCE_WHEEL_FILE=salience.whl \
  /tmp/salience-p1-task22-final-20261029-SI95HA/build-context
```

The successful normal-network local build command was:

```bash
env -i PATH="$PATH" HOME=/home/admin DOCKER_CONTEXT=default \
  docker --context default buildx build --load --progress=plain \
  --metadata-file /tmp/salience-p1-task22-final-20261029-SI95HA/image-metadata.json \
  --tag salience-task22-development-p1-20261029-si95ha:latest \
  --build-arg SALIENCE_WHEEL_FILE=salience.whl \
  /tmp/salience-p1-task22-final-20261029-SI95HA/build-context
```

The package inventory was recorded from the exact local image without network access:

```bash
docker run --network none --rm --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=1m --entrypoint python \
  salience-task22-development-p1-20261029-si95ha:latest \
  -c 'import importlib.metadata as m; print("\n".join(sorted("{}=={}".format(d.metadata.get("Name"), d.version) for d in m.distributions() if d.metadata.get("Name"))))' \
  > /tmp/salience-p1-task22-final-20261029-SI95HA/resolved-runtime-packages.txt
```

The exact deterministic route generator is retained as `/tmp/salience-p1-task22-final-20261029-SI95HA/build-task22-route-bundle.py`. Its command form is:

```bash
PYTHONPATH=src .venv/bin/python \
  /tmp/salience-p1-task22-final-20261029-SI95HA/build-task22-route-bundle.py \
  /tmp/salience-p1-task22-final-20261029-SI95HA/route-bundle.json \
  7e3073d66ffc6f6d0eb9aa4a27e08e506d82b7ecf58d749558cd6544d805a5b6 \
  24084cf14fadcb711bccefb9ab24c72444c0740ad819d79a3756831b2e008fd6
```

After commit, the same helper was run to a second file and `cmp` confirmed that its output exactly matched the committed build's route bundle.

Cleanup targeted only the exact disposable objects:

```bash
env -i PATH="$PATH" HOME=/home/admin DOCKER_CONTEXT=default \
  docker --context default rm salience-task22-runtime-extract-20261029-si95ha
env -i PATH="$PATH" HOME=/home/admin DOCKER_CONTEXT=default \
  docker --context default image rm salience-task22-development-p1-20261029-si95ha:latest
```

Both exact objects were then verified absent. No prune command was run.
