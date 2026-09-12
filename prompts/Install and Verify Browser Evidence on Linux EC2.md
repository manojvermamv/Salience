Before starting Phases 7–8, complete the production-compatible browser evidence setup on the current Linux EC2 host.

Inspect the existing repository, OS/distribution, Python environment, Playwright version, Docker state, and available disk space first. The project currently pins `playwright==1.62.0`; preserve that version unless compatibility verification proves a change is required.

Do not perform destructive global Docker/system cleanup without explicit approval.

Goal:

`install compatible Chromium -> verify Playwright runtime -> exercise Salience BrowserWorker -> capture real evidence -> run automated end-to-end browser tests`

Use the official Playwright installation path appropriate for the detected Linux distribution. Install only the required Chromium/browser dependencies; do not install Firefox/WebKit unless the project actually needs them.

Keep browser execution:
- headless by default;
- read-only;
- isolated per run;
- domain/HTTPS allowlisted;
- bounded by timeout, navigation and network limits;
- unable to access unrelated secrets or localhost/private-network destinations unless explicitly allowed;
- downloads disabled by default.

Implement/verify automated tests proving:

1. Playwright launches successfully on the EC2 host.
2. Chromium version/runtime is recorded.
3. A safe allowlisted JavaScript page can be opened.
4. The existing canonical browser adapter returns structured evidence.
5. Page text is stored as an artifact/reference rather than trusted instruction.
6. Screenshot evidence is captured.
7. Playwright trace evidence is captured.
8. Source URL, fetch time, tool/browser version, agent/tool run IDs and artifact hashes are persisted.
9. An unapproved domain is blocked.
10. A redirect to an unapproved/private destination is blocked.
11. Downloads/write actions remain denied.
12. Timeout/failure produces useful trace evidence without leaking secrets.
13. Browser-retrieved hostile/prompt-injection text remains untrusted and cannot grant authority or become verified memory.
14. The browser test can run from one repeatable command in CI/operator workflows.

Prefer using the project's existing `BrowserWorker`/research contracts. Do not introduce a second browser architecture.

Add a focused command such as:

`bash scripts/verify-browser-evidence.sh`

or an equivalent project-standard command that:
- checks prerequisites;
- verifies the installed browser;
- runs the browser integration/security tests;
- reports PASS / FAIL / NOT RUN clearly;
- leaves useful evidence paths in the output.

Do not require paid APIs or credentials.

Do not begin Phases 7–8 in this task.

At completion, report:
- detected OS;
- free disk before/after;
- Playwright version;
- installed Chromium version;
- installation command used;
- automated test results;
- screenshot/trace evidence locations;
- any remaining EC2/browser limitation.

Completion criterion:

**A real headless Chromium session runs on the EC2 host through Salience's governed browser contract and passes the automated evidence, isolation, allowlist, trust and failure-trace tests.**