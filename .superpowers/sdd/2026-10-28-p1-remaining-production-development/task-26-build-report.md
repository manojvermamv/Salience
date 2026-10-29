# Task 26 exact candidate build report

Date: 2026-10-29

## Result and Git identity

Built the final development-only v2 publication-worker launch lock from frozen source commit `cf070328d035f9010b760431173845c6afdfedee` (tree `d9f57a83364422b2062fdfbe1cf03a87c9af71a0`). After the round-three typed image-identity checks passed, the existing unpushed artifact commit was amended in place. Final artifact commit: `33cac5cffff973e9cba5e03be6b493065e5f817d`, tree `d324a3257c9149ee65b52a95333ebf9a57ceb8f2`, with the exact source commit as its sole parent. It changes only:

- `deployment/v4/publication-worker-launch-lock-v2-development.json`
- `deployment/v4/publication-worker-launch-lock-v2-development.sha256`

The final `arch` worktree is clean. `git diff --check cf070328d035f9010b760431173845c6afdfedee HEAD` passed; the amended commit's parent is exactly the source commit and its changed path list contains only the two files above. The v1 lock at `deployment/v4/publication-worker-launch-lock.json` remains byte-identical to source; SHA-256 `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c`. No push or merge occurred.

## Candidate identities and digests

- Source commit/tree/parent: `cf070328d035f9010b760431173845c6afdfedee` / `d9f57a83364422b2062fdfbe1cf03a87c9af71a0` / `7641f6cf1d5d889146b5ff5153ed5b73968ae78c`.
- Source commit timestamp used for `SOURCE_DATE_EPOCH`: `1793272876`.
- Source archive SHA-256: `b92d7cf53ad9fe597de01ebb119ac6bc7f4085ecbf85c7562a8d6e74bbdb7f25`. Two independent clean archive exports compared equal.
- Source manifest has 250 file pins; canonical source-manifest SHA-256: `6d5c6e5b30487a01fb15e61345774b124d5d7db9f43eebd1e7babefd7c83be37`. The verifier recomputed every content SHA-256 and Git blob ID directly from the source commit.
- Both independently built wheels are 459,327 bytes and have SHA-256 `0c44f28534e1976e13429b3ae14df02acb4f7329e46ff824861489f574c078c6`; byte comparison passed.
- Final selected single-platform image descriptor: `sha256:ccf73a53169b1e775036afda13b858114b2201905bab155e1bd8036ca49078e0`, media type `application/vnd.oci.image.manifest.v1+json`, size 2,951 bytes. The config is separately identified as `sha256:03ad31fde9e2a0ed843a79639a01a5baf8d1a22d53673790c7df4e6ccff28ecd`; it is not the route image digest. Two retained provenance-disabled OCI outputs agree on the selected manifest, config, all 14 layers, and every referenced blob. Their root `index.json` annotations and archive bytes differ, so no archive-reproducibility claim is made.
- The original candidate's BuildKit OCI index (`7bf80754…`) and the first fix-loop rebuild index (`14027eb2…`) are historical outer-index values. Neither is the final route value; the v2 route uses the typed single-platform manifest descriptor `ccf73a…`.
- Extracted interpreter SHA-256: `73b68fc2dac176af16098180a2c98a03ee9f1f1b02db25d42d44d782e79e2330`.
- Installed Salience distribution RECORD SHA-256: `84891770e3c0bcd8d0d1f1115c6a6df683fd90fb966bdf0c8ca422e2e01731aa`.
- Runtime inventory: 73 distributions, SHA-256 `349782ff04770a3543668323fd5bcd88783e5b2b34fb90ce6a7cf1f3f2867c06`, retained as `resolved-runtime-packages.txt` in the evidence directory (same bytes as the Task 22 inventory).
- Final synthetic route bundle SHA-256: `55618bd0f2515421407e1ce1e2723952d9261efdf9c5bafe9c48bbe2e034ccac`; tuple SHA-256: `a472211d2bd22d735c711cf99b9d296de15921368a893ba96dc89e29ec89c9cc`; route-manifest SHA-256: `88fc34cc722d05f6c160a4842f09b4525aebd8f09bb205adcbcefb4eeb25e883`.
- Final canonical v2 lock JSON SHA-256 and companion content: `c9cdea795fe6342c5bad052a445c5b9459434df58e7fabb93f13bc83e1109fbd`.
- Final v2 companion file SHA-256: `5cc49a4edcb158d3abdd397c62e1a65d5f0b449908fbff852cf915f27a9c66de`.
- Two independent round-three lock-builder runs produced byte-identical lock JSON, digest, control lock, control digest, and launcher. Both emitted the same source-manifest SHA above. The earlier `7e63…` lock was superseded by the amended artifact commit.

## Image identity fix-loop evidence

The review found that BuildKit's `containerimage.digest` is the manifest/index identity, while Docker's `.Id` is intended to identify the image config. The original verifier compared those different types. The fix-loop rebuilt only the offline Task26 image with the same local Task15 base and exact task-owned tag, using `--pull=false --network=none`; it did not rebuild the wheel, route, or lock.

| Evidence | First candidate build | Offline fix-loop rebuild |
| --- | --- | --- |
| BuildKit descriptor media type | `application/vnd.oci.image.index.v1+json` | `application/vnd.oci.image.index.v1+json` |
| BuildKit manifest/index digest | `sha256:7bf80754db1d96a3f7d574c3a808998a8b3931ec316019c1a14f3efd93b12fa6` | `sha256:14027eb2e36a499946bc80ae45179a50b4d102dec4eb66bd15e4c8b734cc6bf0` |
| BuildKit exported config digest from build log | `sha256:03ad31fde9e2a0ed843a79639a01a5baf8d1a22d53673790c7df4e6ccff28ecd` | `sha256:03ad31fde9e2a0ed843a79639a01a5baf8d1a22d53673790c7df4e6ccff28ecd` |
| Docker `.Id` retained as raw inspect evidence | Not retained; the old verifier asserted it equaled the manifest index, which was an invalid cross-type comparison | `sha256:14027eb2e36a499946bc80ae45179a50b4d102dec4eb66bd15e4c8b734cc6bf0` |
| Manifest equals lock/route digest `7bf80754…` | Yes, from first BuildKit metadata | **No** |
| Rebuild Docker `.Id` equals config digest `03ad31fd…` | Not established by first-build evidence | **No** |

The fresh build log confirms the config digest, and its metadata confirms the index digest. The daemon inspect JSON records `.Id` as the index digest in this environment, not the config digest. These observed values do not satisfy the requested independent image checks. The exact evidence JSON is `image-identity-review-evidence.json`; the corrected verifier outputs show both mismatches and exit status 1. No lock or Git commit was rewritten.

Fix-loop evidence SHA-256 values: `image-identity-review-evidence.json` `081bd7526656326fbe968f71e66bdad238a835525208bc8f89519e910d348507`; `image-metadata-rebuild.json` `c947f148488f0964e5359a091013d825d5ed5b59a6f32f4ee9243c7a8c4c5e5b`; `image-build-rebuild.log` `08145c5cabfeed9dd356a2c98d52009b30a8b70159dc1682d428212e3c964b7e`; `image-inspect-rebuild.json` `edcc8593269d9477e3e4e53e4bbb3926d950413caf8756da6d00a3f24fe6934f`; corrected pre-commit verifier `verify-task26-artifacts.py` `583d2fd6c3dba6219a837a4db5cf4490da9aa816a626e4a1c777f71fda2453ca`; corrected post-commit verifier `verify-task26-postcommit.py` `6eaadf39e495eeb21733f94e4aed72cc24bdc4c9bc5e9b7b499d84602d82d59e`; cleanup/filter transcript `task26-cleanup-transcript.txt` `eda854288b256192909469618e761ab4e3c05890180d5b1a4b9e414f718bc80b`.

## Task 27 deterministic OCI recovery attempt (round two; historical gate)

The task-owned build context and wheel were confirmed unchanged before rebuilding: context wheel and both independent wheel files are byte-identical at SHA-256 0c44f28534e1976e13429b3ae14df02acb4f7329e46ff824861489f574c078c6; alembic.ini matches source; migrations compare recursively equal to source; and the temporary offline Dockerfile SHA-256 is fceb417a158322480e90dd8a2a8e7bed71b08461b271404e280a6d1a8499d7e4. The cached Task15 base remains local at image ID sha256:51f38f1ae8c31c86601eda50116f4e19a0b68915dcbdfcdd708bb2e6f62cd79d. Git remained clean at artifact commit ffe640f1f50c6cc7ba16cbd85d346e13a09feb15, whose direct parent is the required source commit.

Two independent invocations used the same context, wheel, tag, label, platform, offline settings, and OCI output type. Neither used --load:

~~~bash
docker --context default buildx build --pull=false --network=none --platform=linux/amd64 --provenance=false --progress=plain \
  --metadata-file /tmp/salience-p1-task26-final-20261029-1pPfji/image-metadata-stable-1.json \
  --tag salience-task26-development-p1-20261029-1ppfji:latest \
  --label com.salience.task=task26 \
  --build-arg SALIENCE_WHEEL_FILE=salience.whl \
  --file /tmp/salience-p1-task26-final-20261029-1pPfji/build-context/Dockerfile.task26-offline \
  --output type=oci,dest=/tmp/salience-p1-task26-final-20261029-1pPfji/image-stable-1.oci.tar \
  /tmp/salience-p1-task26-final-20261029-1pPfji/build-context \
  > /tmp/salience-p1-task26-final-20261029-1pPfji/image-build-stable-1.log 2>&1

docker --context default buildx build --pull=false --network=none --platform=linux/amd64 --provenance=false --progress=plain \
  --metadata-file /tmp/salience-p1-task26-final-20261029-1pPfji/image-metadata-stable-2.json \
  --tag salience-task26-development-p1-20261029-1ppfji:latest \
  --label com.salience.task=task26 \
  --build-arg SALIENCE_WHEEL_FILE=salience.whl \
  --file /tmp/salience-p1-task26-final-20261029-1pPfji/build-context/Dockerfile.task26-offline \
  --output type=oci,dest=/tmp/salience-p1-task26-final-20261029-1pPfji/image-stable-2.oci.tar \
  /tmp/salience-p1-task26-final-20261029-1pPfji/build-context \
  > /tmp/salience-p1-task26-final-20261029-1pPfji/image-build-stable-2.log 2>&1

mkdir -p /tmp/salience-p1-task26-final-20261029-1pPfji/oci-stable-1 /tmp/salience-p1-task26-final-20261029-1pPfji/oci-stable-2
tar -xf /tmp/salience-p1-task26-final-20261029-1pPfji/image-stable-1.oci.tar -C /tmp/salience-p1-task26-final-20261029-1pPfji/oci-stable-1
tar -xf /tmp/salience-p1-task26-final-20261029-1pPfji/image-stable-2.oci.tar -C /tmp/salience-p1-task26-final-20261029-1pPfji/oci-stable-2
cmp /tmp/salience-p1-task26-final-20261029-1pPfji/oci-stable-1/oci-layout /tmp/salience-p1-task26-final-20261029-1pPfji/oci-stable-2/oci-layout
cmp /tmp/salience-p1-task26-final-20261029-1pPfji/oci-stable-1/index.json /tmp/salience-p1-task26-final-20261029-1pPfji/oci-stable-2/index.json
diff -qr /tmp/salience-p1-task26-final-20261029-1pPfji/oci-stable-1/blobs/sha256 /tmp/salience-p1-task26-final-20261029-1pPfji/oci-stable-2/blobs/sha256
diff -qr /tmp/salience-p1-task26-final-20261029-1pPfji/oci-stable-1 /tmp/salience-p1-task26-final-20261029-1pPfji/oci-stable-2
~~~

Both builds completed offline with pull disabled. The OCI archive SHA-256 values differ: build 1 is 3bf087d70d6a18d72af0413e554e3592ed854968757c8e4ba533e54ea5c65fed; build 2 is 869095059b7afb8a7ec24823c4a420bb3b3fe7db7285d9052adb52ab237f9760.

Retained build evidence hashes: metadata 1 `3e653756e7657921be48bdd0434b8440daccc11cfd5db70737ed98b1a303cf2f`; metadata 2 `912f469ba7847f9f7b890561a412b55e5e9e738e50b1451b523db9e6717d4190`; log 1 `8f6fbff04342c04bcce8db09190f51490d31a7d42cfa009bd33db5f0c69fbd24`; log 2 `b35773ae6e36336ee88558dda2ffcc83f4f518c3f8b5ee1c6a4689341d8c28cf`; input identity checks `17d402361b4c9c755ec47bb08c15208a81fd0775f6e084a1564bdf4586120aee`; extracted full-layout diff `ebf394ac0d326975282ed85c12ad781ba3cf2dbf6b00f9e823221d34dad72a06`; cleanup/filter transcript `50f1fb8d5ac1694c52fbd14264780e26aeb0fba9135363cdb83d26bd729adca0`.

The typed OCI parser verified each archive's OCI layout version 1.0.0; root index media type application/vnd.oci.image.index.v1+json with exactly one linux/amd64 image-manifest descriptor and no attached attestation descriptor; child manifest media type application/vnd.oci.image.manifest.v1+json, digest sha256:ccf73a53169b1e775036afda13b858114b2201905bab155e1bd8036ca49078e0, size 2,951 bytes; config media type application/vnd.oci.image.config.v1+json, digest sha256:03ad31fde9e2a0ed843a79639a01a5baf8d1a22d53673790c7df4e6ccff28ecd, size 9,818 bytes, platform linux/amd64; and all 14 layer blobs by media type, size, and SHA-256. BuildKit containerimage.digest and descriptor digest/type/size match the parsed child manifest; its config digest matches the parsed config. Every referenced blob hashes correctly, and blob directories are byte-identical.

The round-two whole-layout gate failed because `index.json` differs. Its SHA-256 is `96bcb427232c12c25d5538ce81780c68b659a17ee178f97160a633de4094c674` for build 1 and `4b7682903046475fffa2a9d01c56925861339d330fc1edb5aee5f84a65b4c6fb` for build 2. The selected image manifest is `ccf73a…` in both builds, but the root OCI index descriptor annotations have `created` values `2026-10-29T11:51:24Z` and `2026-10-29T11:51:33Z`. A normalized comparison confirmed this is the only parsed index JSON difference. The earlier verifier's field `oci_layout_byte_equal=true` meant only that the `oci-layout` version file was equal; it did not mean the full layout package was byte-identical. This round-two strict package-equality result was superseded for route image identity by the Task27 round-three addendum below. It remains accurate evidence that the complete OCI layout and archives are not byte-reproducible.

Historical round-two verifier results: the `oci-layout` version file, blob sets, blob bytes, manifest, config, layers, and metadata identity compared equal; raw `index.json` did not. The verifier exited 1 because it required full-layout equality. Its evidence JSON SHA-256 was `cc91519efd939eff5a1500a83a8146ee2e2555dd891b6b782a6c87f18b603ff7`; verification output SHA-256 was `4552aa39733dff4106e646981342d346192aff06f204d05c2ad4f2e7dc6cb32c`.

At the end of round two no route or lock had been regenerated; the original v2 lock and artifact commit were still in place. Round three then accepted the selected image artifact using typed manifest identity while preserving the raw package mismatch as a limitation. The final route and amended lock results are recorded in the next section.

## Round three typed image identity and final artifact amendment

No image build was run in round three. The two retained provenance-disabled OCI outputs were re-evaluated with a typed artifact-identity verifier at `/tmp/salience-p1-task26-final-20261029-1pPfji/verify-task26-typed-image-identity-round3.py`. It hashes each manifest/config/layer blob, checks declared sizes and media types, verifies the one-entry linux/amd64 root descriptor against both BuildKit metadata files, checks there are exactly 14 layers and no extra blobs, and compares the referenced payload bytes across outputs. It reports the full OCI package/archive byte inequality separately from route-image identity.

The exact verifier invocation was:

```bash
/home/admin/Salience/.venv/bin/python \
  /tmp/salience-p1-task26-final-20261029-1pPfji/verify-task26-typed-image-identity-round3.py \
  /tmp/salience-p1-task26-final-20261029-1pPfji \
  > /tmp/salience-p1-task26-final-20261029-1pPfji/typed-image-identity-round3.txt 2>&1
```

It exited 0. Exact output: `typed-image-identity-round3.txt` SHA-256 `ab8b1954207fceaed34f14abf07b86ee5ab2374531f1f5b39f94f85fa77f3091`; verifier script SHA-256 `b8f52362c56710bcafbc476c170090c1d5635990b7e790454868039597b7f6c4`; structured evidence JSON SHA-256 `c4aa2822b8f58a994ecf33a3ca067bd14fe3e5f6db4aa40ada2718246d5da6f9`. The typed output confirms:

```text
buildkit_typed_descriptor_identity_equal=true
selected_linux_amd64_descriptor_identity_equal=true
manifest_config_14_layers_equal=true
all_referenced_blob_sets_equal=true
all_referenced_blob_bytes_equal=true
root_index_json_byte_equal=false
root_index_diff_only_created_annotation=true
root_index_created_annotations=["2026-10-29T11:51:24Z","2026-10-29T11:51:33Z"]
oci_layout_byte_equal=false
oci_archive_byte_equal=false
route_image_identity_equal=true
oci_archive_reproducibility_claim=false
```

For each build, BuildKit `containerimage.digest` and `containerimage.descriptor.digest` are `sha256:ccf73a53169b1e775036afda13b858114b2201905bab155e1bd8036ca49078e0`; descriptor media type is `application/vnd.oci.image.manifest.v1+json`, size 2,951. Each root index has exactly one linux/amd64 entry with the same manifest digest/type/size. The manifest config digest is `sha256:03ad31fde9e2a0ed843a79639a01a5baf8d1a22d53673790c7df4e6ccff28ecd`; the config and 14 layers hash and compare byte-for-byte. All referenced blobs match. Root index SHA-256 values are `96bcb427232c12c25d5538ce81780c68b659a17ee178f97160a633de4094c674` and `4b7682903046475fffa2a9d01c56925861339d330fc1edb5aee5f84a65b4c6fb`; OCI archive SHA-256 values are `3bf087d70d6a18d72af0413e554e3592ed854968757c8e4ba533e54ea5c65fed` and `869095059b7afb8a7ec24823c4a420bb3b3fe7db7285d9052adb52ab237f9760`. The changed root index annotation is packaging nondeterminism. No archive/layout reproducibility claim is made.

The route helper was invoked twice, first from the current clean source import path and then from the detached worktree at the frozen source commit, for `route-bundle-round3.json` and `route-bundle-round3-repeat.json`. Both runs used wheel SHA `0c44f28534e1976e13429b3ae14df02acb4f7329e46ff824861489f574c078c6` and the verified manifest digest without its `sha256:` prefix:

```bash
env -i PATH="$PATH" HOME=/home/admin PYTHONPATH=/home/admin/Salience/src \
  /home/admin/Salience/.venv/bin/python \
  /tmp/salience-p1-task26-final-20261029-1pPfji/build-task26-route-bundle.py \
  /tmp/salience-p1-task26-final-20261029-1pPfji/route-bundle-round3.json \
  0c44f28534e1976e13429b3ae14df02acb4f7329e46ff824861489f574c078c6 \
  ccf73a53169b1e775036afda13b858114b2201905bab155e1bd8036ca49078e0
```

The second invocation used `PYTHONPATH="$R/source-worktree/src"` and wrote `route-bundle-round3-repeat.json`; both bundle and log compared byte-for-byte. Final route bundle SHA-256 is `55618bd0f2515421407e1ce1e2723952d9261efdf9c5bafe9c48bbe2e034ccac`; route tuple SHA-256 is `a472211d2bd22d735c711cf99b9d296de15921368a893ba96dc89e29ec89c9cc`; route-manifest SHA-256 is `88fc34cc722d05f6c160a4842f09b4525aebd8f09bb205adcbcefb4eeb25e883`. The route's `image_sha256` is the manifest digest `ccf73a…`, never the config or either root-index digest.

The lock builder requires its repository `HEAD` to equal `--application-commit`. A temporary detached worktree at the exact source commit was therefore created and removed after the two independent builds. Each run used a fresh `env -i` environment, its independent wheel, the same retained runtime/interpreter inputs, and the round-three route bundle. The command shape was:

```bash
env -i PATH="$PATH" HOME=/home/admin PYTHONPATH="$R/source-worktree/src" \
  /home/admin/Salience/.venv/bin/python \
  "$R/source-worktree/scripts/build_publication_worker_launch_lock.py" \
  --application-commit cf070328d035f9010b760431173845c6afdfedee \
  --wheel "$R/wheels-$index/salience-0.1.0-py3-none-any.whl" \
  --runtime-root "$R/runtime-stage/usr/local/lib/python3.13/site-packages" \
  --lock "$R/locks/stable-$index.json" \
  --independent-digest "$R/locks/stable-$index.sha256" \
  --control-dir "$R/locks/control-stable-$index" \
  --interpreter-source "$R/extracted/image-interpreter" \
  --route-bundle "$R/route-bundle-round3.json"
```

Both runs printed source manifest `6d5c6e5b30487a01fb15e61345774b124d5d7db9f43eebd1e7babefd7c83be37`; their lock JSON, companion, control lock, control digest, and launcher compared byte-for-byte. The canonical lock SHA-256 and companion content are `c9cdea795fe6342c5bad052a445c5b9459434df58e7fabb93f13bc83e1109fbd`; the companion file SHA-256 is `5cc49a4edcb158d3abdd397c62e1a65d5f0b449908fbff852cf915f27a9c66de`.

The independent round-three candidate verifier `/tmp/salience-p1-task26-final-20261029-1pPfji/verify-task26-round3-candidate.py` exited 0 with output SHA-256 `f90c6fc30c2904636f82889533bd48400135bcc7d938b6867db04fcc96212af5`. It rechecked all 250 source pins and Git blob IDs, both wheel hashes and source package members, runtime inventory/source/RECORD/interpreter, typed image evidence, route parsing and IDs, lock controls/companion, development-only qualification, migration head, ABI v2, absent `PRODUCTION_EXTERNAL_TRUST_DIGEST`, and v1 lock bytes. Key verified values: wheel `0c44f285…`; runtime inventory (73 distributions) `349782ff…`; RECORD `84891770…`; interpreter `73b68fc2…`; v1 lock `1c8ac4ff…`; route image `ccf73a…`.

After all candidate checks passed, only the two v2 lock files were copied into the worktree. `git diff --check cf070328d035f9010b760431173845c6afdfedee` passed; only those two paths were staged. The existing artifact commit was amended in place with `git commit --amend --no-edit`, not followed by another commit. Final commit `33cac5cffff973e9cba5e03be6b493065e5f817d` has sole parent `cf070328d035f9010b760431173845c6afdfedee`, tree `d324a3257c9149ee65b52a95333ebf9a57ceb8f2`, and only the required two changed paths. Post-amend checks verified committed bytes equal the independently built pair, the companion matches the canonical lock SHA, v1 remains unchanged, `git diff --check` passes, and the worktree is clean. Exact post-amend transcript `round3-post-amend-verification.txt` has SHA-256 `2c195e0f30cc8407eb772cbbe2fefa8cd6410999f7e334c891691e29beac778a`.

## Development route and trust state

The route is fully synthetic and development-only:

- Deployment: `00000000-0000-0000-0000-000000000261`
- Environment: `00000000-0000-0000-0000-000000000262`
- Route: `00000000-0000-0000-0000-000000000263`
- Slot: `00000000-0000-0000-0000-000000000264`
- Build ID: `p1-task26-development-only`
- Temporal endpoint identifier: `development-temporal.task26.unprovisioned`
- Namespace / task queue: `salience-task26-development` / `salience-publication-task26-development`
- Workflow: `GovernedPublicationWorkflow`; activity allowlist is the source-pinned nine `salience.publication.*` activities.
- Compatibility: `exact-head`; producer bindings: `[]`.
- Qualification: `development-only`; migration head: `0051_p1_recovery_rollback_v2`; rollback verifier ABI: `salience.p1-recovery-rollback-verifier.v2`.
- `PRODUCTION_EXTERNAL_TRUST_DIGEST` was absent from the clean builder environment and is absent from the lock. No production qualification or production effects are enabled.

The final committed route uses wheel digest `0c44f285…` and verified image-manifest digest `ccf73a…`. It remains synthetic, development-only, and pointed at the unprovisioned Temporal endpoint; no producer bindings or production trust value were introduced.

## Build and verification procedure

All evidence is retained under `/tmp/salience-p1-task26-final-20261029-1pPfji`; the root is also recorded in `/tmp/salience-task26-build-root-path.txt`. Initial checkout was clean at the required source commit. The two source trees were separately exported and compared:

```bash
git archive --format=tar cf070328d035f9010b760431173845c6afdfedee | tar -xf - -C /tmp/salience-p1-task26-final-20261029-1pPfji/source-1
git archive --format=tar cf070328d035f9010b760431173845c6afdfedee | tar -xf - -C /tmp/salience-p1-task26-final-20261029-1pPfji/source-2
diff -qr /tmp/salience-p1-task26-final-20261029-1pPfji/source-1 /tmp/salience-p1-task26-final-20261029-1pPfji/source-2
git archive --format=tar cf070328d035f9010b760431173845c6afdfedee | sha256sum
git show -s --format=%ct cf070328d035f9010b760431173845c6afdfedee
```

The host virtualenv ran Python 3.13.5 but did not contain setuptools. To keep wheel construction offline, setuptools 83.0.0 and its `_distutils_hack` were copied from the already-local `openalgo:latest` image (Python 3.12.15) into task-owned `build-tool`, then exposed through `PYTHONPATH`. No image was pulled for this step. Each wheel was built separately with build isolation and dependency resolution disabled, the package index disabled, and the source commit epoch fixed:

```bash
for index in 1 2; do
  env -i PATH="$PATH" HOME=/home/admin PYTHONPATH=/tmp/salience-p1-task26-final-20261029-1pPfji/build-tool PIP_NO_INDEX=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 SOURCE_DATE_EPOCH=1793272876 \
    /home/admin/Salience/.venv/bin/python -m pip wheel --no-build-isolation --no-deps \
    --wheel-dir "/tmp/salience-p1-task26-final-20261029-1pPfji/wheels-$index" \
    "/tmp/salience-p1-task26-final-20261029-1pPfji/source-$index"
done
cmp /tmp/salience-p1-task26-final-20261029-1pPfji/wheels-1/salience-0.1.0-py3-none-any.whl /tmp/salience-p1-task26-final-20261029-1pPfji/wheels-2/salience-0.1.0-py3-none-any.whl
```

### Image build constraint and workaround

The source Dockerfile pins base `python@sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419`, which was not present as a standalone local image. An intermediate offline attempt using the locally available `salience-task15-final-fix@sha256:51f38f1ae8c31c86601eda50116f4e19a0b68915dcbdfcdd708bb2e6f62cd79d` base reference failed when BuildKit attempted a Docker Hub metadata lookup and returned pull access denied. This was a metadata request only and did not pull an image. No attempt was made to pull the unavailable Python base; no image pulls were performed.

To finish without pulling images or reaching package indexes, a temporary `Dockerfile.task26-offline` used the matching locally present image `salience-task15-final-fix:0173915d6ca9` (immutable ID `sha256:51f38f1ae8c31c86601eda50116f4e19a0b68915dcbdfcdd708bb2e6f62cd79d`). It installed only the generated wheel with `--no-index --no-deps` and copied source-commit `alembic.ini` and `migrations`. The build used `--pull=false --network=none`, no registry image pull, and task label `com.salience.task=task26`:

```bash
docker --context default buildx build --load --pull=false --network=none --progress=plain \
  --metadata-file /tmp/salience-p1-task26-final-20261029-1pPfji/image-metadata.json \
  --tag salience-task26-development-p1-20261029-1ppfji:latest \
  --label com.salience.task=task26 \
  --build-arg SALIENCE_WHEEL_FILE=salience.whl \
  --file /tmp/salience-p1-task26-final-20261029-1pPfji/build-context/Dockerfile.task26-offline \
  /tmp/salience-p1-task26-final-20261029-1pPfji/build-context
```

The first build's metadata recorded an OCI index digest, while its build log separately recorded the image config digest. The original retained verifier incorrectly compared Docker `.Id` to the index digest; its prior “verified by immutable image ID” assertion is invalid and has been removed. The fix-loop rebuild and inspection were run as follows. The output metadata and full progress log are retained separately from the original candidate build evidence:

```bash
docker --context default buildx build --load --pull=false --network=none --progress=plain \
  --metadata-file /tmp/salience-p1-task26-final-20261029-1pPfji/image-metadata-rebuild.json \
  --tag salience-task26-development-p1-20261029-1ppfji:latest \
  --label com.salience.task=task26 \
  --build-arg SALIENCE_WHEEL_FILE=salience.whl \
  --file /tmp/salience-p1-task26-final-20261029-1pPfji/build-context/Dockerfile.task26-offline \
  /tmp/salience-p1-task26-final-20261029-1pPfji/build-context \
  > /tmp/salience-p1-task26-final-20261029-1pPfji/image-build-rebuild.log 2>&1
docker --context default image inspect --format '{{json .}}' salience-task26-development-p1-20261029-1ppfji:latest \
  > /tmp/salience-p1-task26-final-20261029-1pPfji/image-inspect-rebuild.json
docker --context default image inspect --format '{{.Id}}' salience-task26-development-p1-20261029-1ppfji:latest \
  > /tmp/salience-p1-task26-final-20261029-1pPfji/image-inspect-id-rebuild.txt
```

The corrected pre-commit/source verifier and post-commit verifier were run independently; each emitted all source/artifact checks and the two false image-identity predicates, then exited 1 with the explicit identity failure:

```bash
PYTHONPATH=/home/admin/Salience/src /home/admin/Salience/.venv/bin/python \
  /tmp/salience-p1-task26-final-20261029-1pPfji/verify-task26-artifacts.py \
  /tmp/salience-p1-task26-final-20261029-1pPfji > /tmp/salience-p1-task26-final-20261029-1pPfji/precommit-verification.txt 2>&1
PYTHONPATH=/home/admin/Salience/src /home/admin/Salience/.venv/bin/python \
  /tmp/salience-p1-task26-final-20261029-1pPfji/verify-task26-postcommit.py \
  /tmp/salience-p1-task26-final-20261029-1pPfji > /tmp/salience-p1-task26-final-20261029-1pPfji/postcommit-verification.txt 2>&1
```

The earlier extraction did copy `/usr/local/lib/python3.13/site-packages`, `/usr/local/bin/python3.13`, and `/usr/local/bin/content` through the named extraction container, and the package inventory was collected inside that image via `docker run --network none --rm --read-only`. Those extracted runtime files still support the recorded interpreter, RECORD, and package-inventory hashes; they do not establish image identity by `.Id`.

The image remains an offline derivative of the locally cached Task 15 runtime, not a direct build from the source Dockerfile's unavailable standalone Python base digest. The two round-three provenance-disabled OCI outputs establish the same selected single-platform manifest/config/layer payload, but their root index annotations and archive bytes differ. The earlier `--load` invocation-specific outer-index digests also differ. No claim of source-Dockerfile fidelity, OCI archive reproducibility, or final worker start-command verification is made. The round-two statement that the committed lock was unchanged was historical; round three amended the artifact commit as documented above.

### Initial Task26 route bundle and lock builds (pre-review; superseded)

The retained deterministic route helper `build-task26-route-bundle.py` was initially run from the clean source import path with wheel and the then-selected OCI index SHA-256 value:

```bash
PYTHONPATH=/home/admin/Salience/src /home/admin/Salience/.venv/bin/python \
  /tmp/salience-p1-task26-final-20261029-1pPfji/build-task26-route-bundle.py \
  /tmp/salience-p1-task26-final-20261029-1pPfji/route-bundle.json \
  0c44f28534e1976e13429b3ae14df02acb4f7329e46ff824861489f574c078c6 \
  7bf80754db1d96a3f7d574c3a808998a8b3931ec316019c1a14f3efd93b12fa6
```

The lock builder was invoked twice with `env -i`, separate wheel path, lock output, digest, control directory, and the same extracted runtime evidence. This kept `PRODUCTION_EXTERNAL_TRUST_DIGEST` absent:

```bash
for index in 1 2; do
  env -i PATH="$PATH" HOME=/home/admin PYTHONPATH=/home/admin/Salience/src \
    /home/admin/Salience/.venv/bin/python \
    /home/admin/Salience/scripts/build_publication_worker_launch_lock.py \
    --application-commit cf070328d035f9010b760431173845c6afdfedee \
    --wheel "/tmp/salience-p1-task26-final-20261029-1pPfji/wheels-$index/salience-0.1.0-py3-none-any.whl" \
    --runtime-root /tmp/salience-p1-task26-final-20261029-1pPfji/runtime-stage/usr/local/lib/python3.13/site-packages \
    --lock "/tmp/salience-p1-task26-final-20261029-1pPfji/locks/lock-$index.json" \
    --independent-digest "/tmp/salience-p1-task26-final-20261029-1pPfji/locks/lock-$index.sha256" \
    --control-dir "/tmp/salience-p1-task26-final-20261029-1pPfji/locks/control-$index" \
    --interpreter-source /tmp/salience-p1-task26-final-20261029-1pPfji/extracted/image-interpreter \
    --route-bundle /tmp/salience-p1-task26-final-20261029-1pPfji/route-bundle.json
done
```

Both successful runs printed source-manifest SHA `6d5c6e5b30487a01fb15e61345774b124d5d7db9f43eebd1e7babefd7c83be37`. The v2 output is canonical JSON with trailing newline; its companion is precisely its SHA-256 followed by a newline. After the image-identity review, both retained verifier scripts were corrected to distinguish the BuildKit manifest/index digest from the BuildKit config digest and Docker `.Id`. Their previous versions and outputs are preserved with `.pre-image-identity-fix` suffixes for audit; the old `.Id == manifest` assertion is invalid. The corrected runs revalidated source pins, manifest, wheel/runtime and RECORD hashes, route tuple and manifest, lock/companion, trust state, v1 bytes, independent lock outputs, commit parent, exact changed paths, and worktree cleanliness. They then reported and exited 1 on both actual image-identity mismatches: the rebuilt manifest differs from the lock/route manifest, and this daemon's `.Id` differs from the BuildKit config digest. Corrected outputs are `precommit-verification.txt` and `postcommit-verification.txt`. The post-commit route-generator copy remains byte-identical to the original route bundle.

Only the initial generated lock pair was staged and committed:

```bash
git add -- deployment/v4/publication-worker-launch-lock-v2-development.json \
  deployment/v4/publication-worker-launch-lock-v2-development.sha256
git commit -m "build: regenerate Task26 development v2 launch lock"
```

No project tests were run, as requested.

## Cleanup and retained evidence

The Task27 OCI exporter used --output=type=oci without --load, so it created no daemon image or container; no image/container removal command was applicable. Post-build label/name filters returned no resources. The exact commands and output are retained in /tmp/salience-p1-task26-final-20261029-1pPfji/task27-oci-cleanup-filter-transcript.txt (SHA-256 50f1fb8d5ac1694c52fbd14264780e26aeb0fba9135363cdb83d26bd729adca0). The earlier Task26 exact-image cleanup command/output remain in task26-cleanup-transcript.txt. OCI archives and extracted layouts are retained for review. BuildKit cache was left intact; no global prune was run.

The fix-loop rebuild used the same exact task-owned image tag and was removed with:

```bash
docker --context default image rm salience-task26-development-p1-20261029-1ppfji:latest
```

The recorded output is `Untagged: salience-task26-development-p1-20261029-1ppfji:latest` and `Deleted: sha256:14027eb2e36a499946bc80ae45179a50b4d102dec4eb66bd15e4c8b734cc6bf0`. The transcript at `/tmp/salience-p1-task26-final-20261029-1pPfji/task26-cleanup-transcript.txt` retains that command/output and post-cleanup label/name filters. Container, image, volume, and network filters for `com.salience.task=task26` and name/image-reference filters for `salience-task26` all returned no resources. No extraction container was created during this fix-loop rebuild. Pip temporary caches were already absent. Python bytecode caches were removed only under the task-owned temporary root. Build evidence and corrected verifier scripts/outputs remain under `/tmp/salience-p1-task26-final-20261029-1pPfji`. BuildKit cache was left intact; no global prune was run.

Round three created no daemon image/container because it only re-evaluated retained OCI archives and regenerated route/lock artifacts. The temporary source worktree was removed with `git worktree remove --force /tmp/salience-p1-task26-final-20261029-1pPfji/source-worktree`; a subsequent worktree-list filter confirmed it was unregistered. The round-three daemon filter transcript is `/tmp/salience-p1-task26-final-20261029-1pPfji/task26-round3-cleanup-filter-transcript.txt`, SHA-256 `2d945c6b2d68c37a2fa4fb23513e1bc26acca5fd4b58035bb8e6387452762684`. Its container label/name filters, image label/reference filters, volume label filter, and network label filter all returned no rows. No removal command was needed in round three. The OCI archives/layouts and verifier evidence remain retained. Task-owned source worktree cleanup was performed; no global image/cache prune was run and BuildKit cache was left intact.

No project tests, push, or merge occurred. The two retained OCI builds used `--pull=false --network=none`; round three ran no Docker build and contacted no live/provider/external acceptance service. No image was pulled. The earlier denied Docker Hub metadata lookup is disclosed above. Final artifact commit is `33cac5cffff973e9cba5e03be6b493065e5f817d`, directly parented by `cf070328d035f9010b760431173845c6afdfedee`; its only changes are the v2 lock JSON and companion. The round-two full-package equality gate failed; round three accepted the stable typed manifest identity and amended the existing artifact commit while preserving and disclosing root-index/archive packaging nondeterminism.
