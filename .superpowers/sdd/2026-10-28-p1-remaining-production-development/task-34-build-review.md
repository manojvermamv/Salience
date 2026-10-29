# Salience Task34 build artifact review

Reviewed source `546ac4ed794afc123ea02586a37d4a4457590582`, artifact `fe6b3795ce4453beb6c6bbafcc20cd3be91b3b87`, and retained evidence under `/tmp/salience-task34-candidate-20261029-i0Yovoin`.

**Spec: PASS**<br>
**Quality: PASS**<br>
**Findings: CLEAN**

The artifact commit is directly parented by the specified source commit and changes exactly the v2 development lock JSON and its SHA companion. The source and artifact tree IDs match the report. The canonical lock is 52,067 bytes with SHA-256 `d5b8e5b7ce0a97ce92bc2259fc2961ad23bc38808262159ca9e1c136c8cc6317`; the companion file hash is `6a91e79eb67b6fa779deafff1c48f1e1d5b2f6bad7ab280469a75fe3f08d01c0`. Both independent lock outputs and their control outputs match. The unchanged v1 lock has SHA-256 `1c8ac4ff92e589e27dc0af2c6dd0bc69c4295435df5a318174a2c77f3b1f2c9c` at both commits.

I recomputed the source manifest and checked each of its 250 unique pins against the source commit’s tracked path set, Git blob ID, SHA-256, and both source exports. The manifest hash is `bc73519f454a95ada057dae27b6a71ac1c0ad1b73db419175666b23e65c410d4`. The two source exports are byte-identical at SHA-256 `20fd5248161ef33bc6d5e4dbeb4f359a25fde1142bf86736c9cda75fb90af565`. Both 459,449-byte wheels are byte-identical at SHA-256 `49d5c34e7d6770d963e6dc065a6b058fe6e08e8880bf8ea0ebbffd34e072d12f`; all 194 package members match the pinned source.

I checked both OCI archives against their extracted layouts and verified each selected Linux/amd64 descriptor, manifest, config, all 14 layer digests and sizes, and every referenced blob byte. Both select manifest `sha256:f1c69d4a45caa33310162335eaf084057017e667b46fc925f93a6ea3b9f9b4af` (`application/vnd.oci.image.manifest.v1+json`, 2,951 bytes), config `sha256:b93060c5466a0a90b4c3b2e0fcff8dc77c087748dc43100cdc6e23949a039f7a`, with matching referenced blob sets and bytes. The root-index hashes differ only in `org.opencontainers.image.created`; the OCI archive hashes also differ. The report correctly makes no archive reproducibility claim and pins the selected manifest digest.

The build used an offline temporary Dockerfile and cached Task15 base `salience-task15-final-fix:0173915d6ca9`, digest `sha256:51f38f1ae8c31c86601eda50116f4e19a0b68915dcbdfcdd708bb2e6f62cd79d`. The source Dockerfile instead names `python@sha256:4c2cf9917bd1cbacc5e9b07320025bdb7cdf2df7b0ceaccb55e9dd7e30987419`; the report correctly says the offline build does not establish fidelity to that unavailable base.

The lock binds to the two identical wheel builds and route bundle. I recomputed route bundle, tuple, and manifest hashes as `2bfbcd68ae8f76cc612cfb58f2000c49ef5167a0537200c42e6256a4903885d9`, `4e49460c7a89ea69a2268fc4d680906be56635a9983f20d1ababb1da4f75714f`, and `40198749291c758d22815def2281a7c5b6860ae8bb43f732d6f42e3765cf2a78`. The route uses synthetic IDs ending `0341`–`0344`, build ID `p1-task34-development-only`, `exact-head` compatibility, empty producer bindings, and the unprovisioned endpoint `development-temporal.task34.unprovisioned`.

Runtime source files match the pinned commit. The 73-distribution inventory hash is `349782ff04770a3543668323fd5bcd88783e5b2b34fb90ce6a7cf1f3f2867c06`; the installed distribution RECORD hash is `fdc6905faa6d807e3aeda7420fb03341b125f5fa9cc1a6030681716e34ce1183`, and every RECORD path, size, and SHA-256 entry resolves within `/usr/local` and matches. The interpreter hash is `73b68fc2dac176af16098180a2c98a03ee9f1f1b02db25d42d44d782e79e2330`. Migration head `0051_p1_recovery_rollback_v2`, rollback verifier ABI `salience.p1-recovery-rollback-verifier.v2`, and `development-only` qualification are consistent. Both trust digests are null, and the external trust environment variable is absent.

The cleanup transcript hash recomputes to `a706ad180811d06bf91b1bff4c61e02ffaa37440fc720d7acca927cea478fd98`; it records removal of the two exact temporary pip-cache entries, empty Task34 container/image/volume/network sweeps, no OCI load, and no global prune. I reviewed the retained transcript and cleanup script but did not invoke Docker, as instructed. The typed image evidence hash recomputes to `d4ab628431c525adcbb1d66fe44290e589f9b091d89b913235434f3d9686587f`; post-commit verifier output hash recomputes to `67b3048cd5c8cb067b57ffe73fe257b4ba6f98b62a1ef5018a327bd506872e4e`.

No project tests, builds, Docker commands, or network calls were run during this review. The repository worktree remains clean.
