# Task26 final independent artifact review

Reviewed artifact `33cac5cffff973e9cba5e03be6b493065e5f817d` against source `cf070328d035f9010b760431173845c6afdfedee`, using the updated candidate report, Task27 image-determinism plan/addendum, retained round-three OCI evidence, and cleanup transcripts. This was read-only validation; no build, test, Docker, network, or repository edit was performed.

## Verdict

- **Spec: PASS** for the stated synthetic, development-only Task26 artifact.
- **Quality: PASS.** No blocking or major findings. The earlier image-identity finding is resolved by a typed manifest binding and independently checked payload hashes.
- **Findings: INFO only.** The artifact is an offline derivative using the cached Task15 base, not a faithful build from the source Dockerfile's unavailable pinned Python base. OCI layout/archive bytes differ due only to the changing root-index `org.opencontainers.image.created` annotation. Neither limitation is presented as source-Dockerfile fidelity or archive-level reproducibility; final worker startup/live Temporal acceptance is also not claimed.

## Git boundary and lock files

The artifact is a direct child of the required source commit. Its tree is `d324a3257c9149ee65b52a95333ebf9a57ceb8f2`; the source tree is `d9f57a83364422b2062fdfbe1cf03a87c9af71a0`. The complete source-to-artifact diff contains only:

- `deployment/v4/publication-worker-launch-lock-v2-development.json`
- `deployment/v4/publication-worker-launch-lock-v2-development.sha256`

`git diff --check` passes. The worktree is clean, and `git worktree list` shows only the main worktree, so the temporary detached source worktree was removed. The final post-amend transcript independently records the same parent, tree, two paths, clean status, and passing diff check.

I recomputed all 250 source file SHA-256 and Git blob pins from the source commit. They match the v2 lock; its source-manifest SHA-256 is `6d5c6e5b30487a01fb15e61345774b124d5d7db9f43eebd1e7babefd7c83be37`. The source archive hash is `b92d7cf53ad9fe597de01ebb119ac6bc7f4085ecbf85c7562a8d6e74bbdb7f25`, with `SOURCE_DATE_EPOCH=1793272876`.

Both independently built wheels are byte-identical, 459,327 bytes, SHA-256 `0c44f28534e1976e13429b3ae14df02acb4f7329e46ff824861489f574c078c6`; their 194 packaged source members match the source. The runtime evidence records 73 distributions, inventory SHA-256 `349782ff04770a3543668323fd5bcd88783e5b2b34fb90ce6a7cf1f3f2867c06`, interpreter SHA-256 `73b68fc2dac176af16098180a2c98a03ee9f1f1b02db25d42d44d782e79e2330`, and installed distribution RECORD SHA-256 `84891770e3c0bcd8d0d1f1115c6a6df683fd90fb966bdf0c8ca422e2e01731aa`. I compared source Salience package files in the runtime stage and pinned migrations/alembic files in the build context byte-for-byte; both generated control launchers match the source launcher and the lock's launcher hash.

## Typed image identity and retained limits

The Task27 round-three plan correctly distinguishes the deployable image descriptor from its OCI layout locator and config. I independently parsed the two retained OCI archives/layouts and matched each archive member to the extracted layout. Each root index has one `linux/amd64` image descriptor and no attestation descriptor. In both BuildKit metadata files, `containerimage.digest` and `containerimage.descriptor.digest` are the same typed descriptor:

- Image manifest: `sha256:ccf73a53169b1e775036afda13b858114b2201905bab155e1bd8036ca49078e0`, media type `application/vnd.oci.image.manifest.v1+json`, size 2,951 bytes.
- Config: `sha256:03ad31fde9e2a0ed843a79639a01a5baf8d1a22d53673790c7df4e6ccff28ecd`, media type `application/vnd.oci.image.config.v1+json`, size 9,818 bytes, platform `linux/amd64`.
- The manifest's 14 layer blob SHA-256 values, in descriptor order, are:
  `59e22667830bf04fb35e15ed9c70023e9d121719bb87f0db7f3159ee7c7e0b8d`, `19f81082f6f0f5682c46ad2fe5283898a3719396e2e8659b150a99de365e1e95`, `799df5177ae295959cf5eb8486412f5107481f1e7c10f96adf4d438795fbcbc0`, `66cd357cf7a1518288e019f7c1ab383d267fa738ec8962e05a3dc84c614019b5`, `289b4a62af7aabeb23942d0a4f4ca36a021a36c7ba04037e6e23a06e7d848b2a`, `77a595e41773f62dbe762f455f56e0dff6eeff70d94d2e7d07e10da7fa37f29e`, `bc32d0e31694eb5c9a43320201c4d47fd5d6a5736f1c8406ebd915b0d7b5898f`, `e5e38aa2ad83d4b1730d1d401e945870bed07271d2f9836fe1d2b0cfca88e5d3`, `92c36760cdec5a4e98c511a10cccd66aa5bc83522a4c50b1756a0d8243444d5d`, `4f4fb700ef54461cfa02571ae0db9a0dc1e0cdb5577484a6d75e68dc38e8acc1`, `d24b8ce3115d047a4c282c19786145992b1c5f820392e2fb165189a36a2c7261`, `6c7b707804d3e50f1d0f9874249ada05a313d66b3e2f800fc42e473fccd8e83e`, `194da3223291121eac3cd5dd8a9bc7cd94b9fc0f410b04125721ef57194d8263`, `c4f01c0f03a244fa6347571384e50c0be663ddf7101c710dca401ea32fa0d0cd`.

For both exports, every descriptor's declared size and digest matches its blob; manifest, config, layer set, and all referenced blob bytes match. The route uses the typed manifest digest above, not the config digest and not either root index digest. Root `index.json` digests differ (`96bcb427232c12c25d5538ce81780c68b659a17ee178f97160a633de4094c674` and `4b7682903046475fffa2a9d01c56925861339d330fc1edb5aee5f84a65b4c6fb`) because the only parsed JSON difference is the `org.opencontainers.image.created` annotation (`2026-10-29T11:51:24Z` versus `2026-10-29T11:51:33Z`). The archive hashes also differ (`3bf087d70d6a18d72af0413e554e3592ed854968757c8e4ba533e54ea5c65fed` and `869095059b7afb8a7ec24823c4a420bb3b3fe7db7285d9052adb52ab237f9760`). The report correctly states that OCI layout/archive byte reproducibility is false; only selected image descriptor and payload identity are established.

The local cached base deviation is explicit and material: the source Dockerfile's pinned standalone Python base was unavailable, so the offline build used cached Task15 image digest `sha256:51f38f1ae8c31c86601eda50116f4e19a0b68915dcbdfcdd708bb2e6f62cd79d` through a temporary offline Dockerfile. The wheel, runtime files, migrations, and lock evidence are still internally tied to the pinned source and exact built derivative. This does not establish fidelity to the source Dockerfile/base or production-image equivalence, and the report makes no such claim. Likewise, worker start-command/live Temporal behavior was not verified or claimed.

## Route, qualification, and cleanup

The final route bundle SHA-256 is `55618bd0f2515421407e1ce1e2723952d9261efdf9c5bafe9c48bbe2e034ccac`; route tuple SHA-256 is `a472211d2bd22d735c711cf99b9d296de15921368a893ba96dc89e29ec89c9cc`; route-manifest SHA-256 is `88fc34cc722d05f6c160a4842f09b4525aebd8f09bb205adcbcefb4eeb25e883`. Two route-helper invocations and two lock-builder invocations are reported byte-identical, including stable lock/control outputs and launchers.

The committed v2 lock JSON and companion content are both `c9cdea795fe6342c5bad052a445c5b9459434df58e7fabb93f13bc83e1109fbd`; companion-file SHA-256 is `5cc49a4edcb158d3abdd397c62e1a65d5f0b449908fbff852cf915f27a9c66de`. The v1 lock remains byte-identical to source at SHA-256 `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c`.

The route is explicitly development-only and synthetic: deployment/environment/route/slot IDs end in `0261`/`0262`/`0263`/`0264`; its Temporal endpoint is marked unprovisioned; producer bindings are empty; compatibility is exact-head. Migration head `0051_p1_recovery_rollback_v2` and rollback-verifier ABI `salience.p1-recovery-rollback-verifier.v2` match source. `PRODUCTION_EXTERNAL_TRUST_DIGEST` is absent from the lock. No production qualification or external trust is inferred.

The cleanup evidence is retained and hashes match the candidate report: `task26-cleanup-transcript.txt` (`eda854288b256192909469618e761ab4e3c05890180d5b1a4b9e414f718bc80b`), `task27-oci-cleanup-filter-transcript.txt` (`50f1fb8d5ac1694c52fbd14264780e26aeb0fba9135363cdb83d26bd729adca0`), and `task26-round3-cleanup-filter-transcript.txt` (`2d945c6b2d68c37a2fa4fb23513e1bc26acca5fd4b58035bb8e6387452762684`). The first records successful removal of the tagged rebuild (`14027eb2…`) and empty task label/name container, image, volume, and network filters. Round three used OCI output without `--load`, so it introduced no daemon image/container; the task-owned OCI files remain as review evidence. No global Docker/BuildKit cache prune is claimed or shown.

## Findings by severity

- **Major:** none.
- **Minor:** none.
- **Info:** The image is a transparently scoped offline derivative from the cached Task15 base, not a source-Dockerfile-faithful build from the unavailable pinned Python base. The identical selected manifest and payload do not make the OCI layout/archive byte-reproducible; the root index timestamp annotation changes. Startup/live Temporal behavior is outside the evidence and remains unclaimed.

The prior image-identity finding is resolved: the final route value is independently bound to the selected single-platform OCI manifest by media type, digest, size, platform, and the complete verified config/layer payload.
