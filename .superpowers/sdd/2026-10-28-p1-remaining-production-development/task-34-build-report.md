# Salience Task34 development candidate build report

Date: 2026-10-29 UTC<br>
Evidence root: `/tmp/salience-task34-candidate-20261029-i0Yovoin`

## Artifact

Built from clean source commit `546ac4ed794afc123ea02586a37d4a4457590582` (tree `278e2bbf074fdf07b701f87f925c537dd916e118`). Artifact commit `fe6b3795ce4453beb6c6bbafcc20cd3be91b3b87` (tree `124037b25c754751e2447c7d8639800f9f35d238`) is directly parented by the source commit. It changes only:

- `deployment/v4/publication-worker-launch-lock-v2-development.json`
- `deployment/v4/publication-worker-launch-lock-v2-development.sha256`

The canonical v2 lock is 52,067 bytes with SHA-256 `d5b8e5b7ce0a97ce92bc2259fc2961ad23bc38808262159ca9e1c136c8cc6317`; the companion file SHA-256 is `6a91e79eb67b6fa779deafff1c48f1e1d5b2f6bad7ab280469a75fe3f08d01c0`. The unchanged v1 lock SHA-256 is `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c`.

## Reproducible artifacts and identity checks

Two independent `git archive` exports of the source commit were byte-identical: SHA-256 `20fd5248161ef33bc6d5e4dbeb4f359a25fde1142bf86736c9cda75fb90af565`, 23,521,280 bytes. Two offline wheel builds used setuptools 83.0.0 and `SOURCE_DATE_EPOCH=1793294462`; both produced the same `salience-0.1.0-py3-none-any.whl`, 459,449 bytes, SHA-256 `49d5c34e7d6770d963e6dc065a6b058fe6e08e8880bf8ea0ebbffd34e072d12f`.

Two single-platform OCI builds ran with `--pull=false --network=none --platform=linux/amd64 --provenance=false`, exporting OCI archives. They used the locally cached Task15 runtime base `salience-task15-final-fix:0173915d6ca9`, digest `sha256:51f38f1ae8c31c86601eda50116f4e19a0b68915dcbdfcdd708bb2e6f62cd79d`. No image was pulled or loaded into the daemon. BuildKit metadata and the typed OCI descriptor agree across both builds and identify the same selected Linux/amd64 image:

- Manifest digest: `sha256:f1c69d4a45caa33310162335eaf084057017e667b46fc925f93a6ea3b9f9b4af`
- Descriptor media type: `application/vnd.oci.image.manifest.v1+json`; descriptor size 2,951 bytes
- Config digest: `sha256:b93060c5466a0a90b4c3b2e0fcff8dc77c087748dc43100cdc6e23949a039f7a`
- Layer count: 14

The typed verifier checked that both selected manifests, configs, all 14 layers, referenced blob sets, and every referenced blob byte match. Each root index contains one selected image descriptor and no attestation. Root index hashes differ only because `org.opencontainers.image.created` differs (`2026-10-29T17:24:56Z` vs `2026-10-29T17:25:00Z`): `b37e7dc191879c3ea4a28d90734cf6db7593fb3ba5c1c7f484914154b93222ac` and `99dfc1d20c4448d47bc53afa07095f8796380ce062cf373220e56e41490b7df4`. OCI archive hashes also differ: `962643bb06a2d5c3efa9d8fed1fe69e1c6d9d4026f0ca2c10139b80ed0cd664a` and `ea1e617cb72be1482a2f5bd10c952bc5ca38670d78088f2aacb821c082cbea79` (both 107,850,752 bytes). No root-layout or archive reproducibility claim is made. The route pins the selected manifest digest, not a config digest or root-index locator.

This build used an offline temporary Dockerfile based on the cached Task15 image. It does not establish fidelity to the source Dockerfile's unavailable Python base.

Two Task34 route bundles and two v2 lock outputs were generated independently and compared byte-for-byte. Route bundle SHA-256: `2bfbcd68ae8f76cc612cfb58f2000c49ef5167a0537200c42e6256a4903885d9`; route tuple SHA-256: `4e49460c7a89ea69a2268fc4d680906be56635a9983f20d1ababb1da4f75714f`; route manifest SHA-256: `40198749291c758d22815def2281a7c5b6860ae8bb43f732d6f42e3765cf2a78`. The route uses synthetic UUIDs ending `0341` (deployment), `0342` (environment), `0343` (route), and `0344` (slot); build ID `p1-task34-development-only`; unprovisioned Temporal endpoint `development-temporal.task34.unprovisioned`; namespace `salience-task34-development`; queue `salience-publication-task34-development`. Compatibility is `exact-head`, and `producer_bindings=[]`. Its image field binds to the selected manifest digest above.

The lock pins 250 source files with source-manifest SHA-256 `bc73519f454a95ada057dae27b6a71ac1c0ad1b73db419175666b23e65c410d4`. Verification matched source pins to Git blobs and both source exports, checked wheel contents, installed runtime source files, distribution RECORD, and interpreter. Runtime inventory: 73 distributions, SHA-256 `349782ff04770a3543668323fd5bcd88783e5b2b34fb90ce6a7cf1f3f2867c06`; RECORD SHA-256 `fdc6905faa6d807e3aeda7420fb03341b125f5fa9cc1a6030681716e34ce1183`; interpreter SHA-256 `73b68fc2dac176af16098180a2c98a03ee9f1f1b02db25d42d44d782e79e2330`. Migration head is `0051_p1_recovery_rollback_v2`; rollback verifier ABI is `salience.p1-recovery-rollback-verifier.v2`; qualification is `development-only`. Production trust is absent: both trust digest fields are null, and no trust environment variable was supplied.

## Verification and cleanup

The full candidate verifier passed before and after the artifact commit. Postcommit checks confirmed the source parent, artifact tree, exact two changed paths, all source pins and manifests, matching wheel/runtime/interpreter identities, typed OCI descriptor and blob equality, Task34 route and lock identities, unchanged v1 lock, and absent production trust. Project tests were not run, as instructed. No external/provider service was contacted.

The exact Task34 sweep found zero containers by label or name, zero images by label or unique tag, zero volumes, and zero networks. The wheel build's two unique Task34 pip-cache entries were removed and verified absent. OCI outputs were not loaded into the daemon. No global prune was run; shared BuildKit cache and the cached Task15 base were retained. Cleanup transcript SHA-256: `a706ad180811d06bf91b1bff4c61e02ffaa37440fc720d7acca927cea478fd98`. Postcommit verifier output SHA-256: `67b3048cd5c8cb067b57ffe73fe257b4ba6f98b62a1ef5018a327bd506872e4e`; typed image identity evidence SHA-256: `d4ab628431c525adcbb1d66fe44290e589f9b091d89b913235434f3d9686587f`.

## Full selected OCI layer identity table

The typed OCI identity verifier compared both independently exported archives. Each build selects the same manifest and config and all 14 layer digests and byte sizes below; each referenced blob was separately checked against its digest and size in both archives.

| Layer | SHA-256 | Bytes |
| ---: | --- | ---: |
| 1 | `59e22667830bf04fb35e15ed9c70023e9d121719bb87f0db7f3159ee7c7e0b8d` | 28,230,270 |
| 2 | `19f81082f6f0f5682c46ad2fe5283898a3719396e2e8659b150a99de365e1e95` | 3,514,146 |
| 3 | `799df5177ae295959cf5eb8486412f5107481f1e7c10f96adf4d438795fbcbc0` | 12,579,705 |
| 4 | `66cd357cf7a1518288e019f7c1ab383d267fa738ec8962e05a3dc84c614019b5` | 250 |
| 5 | `289b4a62af7aabeb23942d0a4f4ca36a021a36c7ba04037e6e23a06e7d848b2a` | 93 |
| 6 | `77a595e41773f62dbe762f455f56e0dff6eeff70d94d2e7d07e10da7fa37f29e` | 441,131 |
| 7 | `bc32d0e31694eb5c9a43320201c4d47fd5d6a5736f1c8406ebd915b0d7b5898f` | 62,068,415 |
| 8 | `e5e38aa2ad83d4b1730d1d401e945870bed07271d2f9836fe1d2b0cfca88e5d3` | 550 |
| 9 | `92c36760cdec5a4e98c511a10cccd66aa5bc83522a4c50b1756a0d8243444d5d` | 81,387 |
| 10 | `4f4fb700ef54461cfa02571ae0db9a0dc1e0cdb5577484a6d75e68dc38e8acc1` | 32 |
| 11 | `dbec4f415ac36bca7c64b00b89c69d9ae680cda14c904bcd38631aad5c012525` | 442,957 |
| 12 | `7de16a388cf4a091e08f60e529254ca0d473bcfd8499d3e3ee26ec456c3563a6` | 380,055 |
| 13 | `a9869c791fdb37bc48f72aad94a8a4b0521304a9dc239920d2b91cbb2d3ff0f5` | 550 |
| 14 | `531595b3bd1daaf995f7b1452d9a34f92fe752acd6316fba97140b2d0be2587a` | 81,385 |
