# Salience Task31 development candidate build report

Date: 2026-10-29 UTC
Evidence root: `/tmp/salience-task31-candidate-20261029-WNsPkI`

## Artifact

Built from clean source commit `8c31e84c9d71d0043b1ca736d9bc4db05cfeee7d` (tree `8118c869150624292d741847e77c75c60992a61a`). The artifact commit is `cc9d55fd67f10f7f8d831e21b9331ecae08cf039` (tree `c7d22a3df59356274aaef342baed68d44b42d64b`), directly parented by the source commit. It changes only:

- `deployment/v4/publication-worker-launch-lock-v2-development.json`
- `deployment/v4/publication-worker-launch-lock-v2-development.sha256`

The v2 lock content SHA-256 is `0b48c85e6ebad29424416e3604a46bfba3a41acb9f299a36d8b4f1cf440f2bd4` (52,067 bytes); the companion file SHA-256 is `c327480a4975409fe358b20cb896b4ced381d61b82e36b625ee5fcb4c84d28a6`. The unchanged v1 lock content SHA-256 is `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c`.

## Reproducible artifacts and identity checks

Two independently exported source trees were byte-identical. Their source archive SHA-256 is `940f35a28aff258d79bea2850e2c9fd6dcbc71d1032c73d26bde6f765042c9f4` (23,408,640 bytes), built with `SOURCE_DATE_EPOCH=1793285677`. Two offline wheel builds were byte-identical: `salience-0.1.0-py3-none-any.whl`, 459,436 bytes, SHA-256 `607c8278688f318d223d1c8a8d4417b12b6dab313be77ea2a59195f8fc9429ce`.

Two single-platform OCI builds used the locally cached Task15 runtime base `salience-task15-final-fix:0173915d6ca9`, base digest `sha256:51f38f1ae8c31c86601eda50116f4e19a0b68915dcbdfcdd708bb2e6f62cd79d`, with `--pull=false --network=none --platform=linux/amd64 --provenance=false`, exporting OCI layouts. No image was pulled or loaded into the daemon. The BuildKit `containerimage.digest` and typed OCI image descriptor agree across builds and identify the same Linux/amd64 OCI image manifest:

- Manifest digest: `sha256:1e68764b582b1ef853a4ddb8b91ced1c03a6e3d13eb47c714600b6bca2123b4f`
- Descriptor media type: `application/vnd.oci.image.manifest.v1+json`; descriptor size 2,951 bytes
- Config digest: `sha256:cca8c53458a45ec7a0f6244caaa419fc084e9f0753879d936683cfcf7df417a3`
- Layer count: 14

The verifier compared descriptor identity and every selected manifest, config, layer, and referenced blob byte; all matched. The OCI root indexes differ only in their `org.opencontainers.image.created` annotation (`2026-10-29T15:00:08Z` vs `2026-10-29T15:00:22Z`): root index hashes `5e01aba9aade3f9d0b5ccd7ab1fed239b87d9510e9d569c3f3d5555326bebb5f` and `968667f56176c0b935fc69a532de09e67747b98d00a7afad643e5984cdbf78e3`. OCI archive hashes also differ (`4d355eae3d389f9520c0f349d18d6c5c5ab8f8a854838690522948d2077288cf` and `56595bebc1512f4c996de9a9ebe70fa8792dcbadfcade71328ddf3ea79e5870c`; both 107,850,752 bytes). Thus no OCI root-layout or archive reproducibility claim is made. The route pins the stable selected **manifest digest**, never the config digest or root-layout locator. This build used an offline temporary Dockerfile based on the cached Task15 image; it does not establish fidelity to the source Dockerfile's unavailable Python base.

Two routes and two v2 lock outputs were generated independently and compared byte-for-byte. Route bundle SHA-256: `b4e46090c54b23b2ebf79d4dbed13711e919df759789d81ccb94968fcb36c19b`; route tuple SHA-256: `789cdd50c79afb4289ededa5381ec6f57a91bd6f4e5058425fc21a4f70a26b8b`; route manifest SHA-256: `66cf3e5f8a2e98e84a076b14ee8cab400f88825f95731c9c88a7d7ed0ead62ed`. Route and lock use the Task31 synthetic deployment/environment/route/slot UUIDs ending `0311`, `0312`, `0313`, and `0314`; build ID `p1-task31-development-only`; unprovisioned Temporal endpoint `development-temporal.task31.unprovisioned`; namespace `salience-task31-development`; queue `salience-publication-task31-development`. The route uses exact-head compatibility and has `producer_bindings=[]`.

The lock pins 250 source files with source-manifest SHA-256 `831484f13eb3daa3cea94133e9f07ee2df00f2b7bebefdadeca8c417dd5f0205`. Verification matched those pins to Git blobs and checked wheel contents, runtime source files, distribution RECORD, installed package inventory and interpreter. Runtime inventory: 73 distributions, SHA-256 `349782ff04770a3543668323fd5bcd88783e5b2b34fb90ce6a7cf1f3f2867c06`; RECORD SHA-256 `adf21d08172e80c67c0b8311c265634c12d715e313d05d762f165c9af90b6340`; interpreter SHA-256 `73b68fc2dac176af16098180a2c98a03ee9f1f1b02db25d42d44d782e79e2330`. Migration head is `0051_p1_recovery_rollback_v2`, rollback verifier ABI is `salience.p1-recovery-rollback-verifier.v2`, and qualification is `development-only`. Production external trust is absent; both trust digest fields are null and no trust environment variable was supplied.

## Verification and cleanup

The full candidate verifier passed before and after the artifact commit. Post-commit verification checked the exact parent/tree/changed paths, source pins and manifests, wheel/runtime/interpreter identities, OCI typed descriptor and blob equality, route and lock identities, v1 lock, and absent production trust. Project tests were not run, as instructed. No source, test, or documentation files were edited. No external/provider service was contacted.

The temporary Task31 container was removed. The final checks found zero Task31 containers (by label and name), images (by label and unique tag), volumes, or networks. No global prune was run and shared BuildKit cache was retained. Exact commands and outputs are in `/tmp/salience-task31-candidate-20261029-WNsPkI/task31-cleanup-transcript.txt` (SHA-256 `19cc787bf0b42d40a1018efb1b6446933d71c008bb6c4db141126fc16dedca7a`). Post-commit verifier output is `/tmp/salience-task31-candidate-20261029-WNsPkI/candidate-verification-postcommit.txt` (SHA-256 `f8e9ac04d9a7f2309aac6c798cdf0f9f568ec8c5b3dbfd2a56573fde4af9417b`); typed image identity JSON evidence SHA-256 is `efbad003656f96aad573ce4519801cfd4f219a0ab13afb14119cfeb7a81f2dce`.
