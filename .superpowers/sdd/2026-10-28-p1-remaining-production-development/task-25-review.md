# Task 25 DNS timeout implementation review

**Reviewed commit:** `cf070328d035f9010b760431173845c6afdfedee`
**Base:** `7641f6cf1d5d889146b5ff5153ed5b73968ae78c`
**Scope:** Compared the implementation diff, the Task 25 plan, the independent review, and the implementation report. Inspected the production source and unit tests at the cited lines. No tests, builds, Docker commands, or external services were run, as required by the review task.

## Verdict

**Spec: PASS**
**Quality: PASS — CLEAN**
**Findings: 0 Critical / 0 Important / 0 Minor**

## Review notes

The change addresses the reported async DNS escape at the `asyncio.run()` boundary. `_pin()` reads `connection.info.host` and `connection.info.hostaddr` while the successful synchronous connection is still open and returns them in a frozen `_DatabaseEndpoint` (`src/salience/cycles/temporal_schedule_control.py:50-80`). `_plan_async()` validates the endpoint before starting a connection: TCP endpoints require an address accepted by `ipaddress.ip_address()`, while a slash-prefixed Unix-socket path may omit `hostaddr` (`:82-97`). It then passes the captured `host` and, when present, `hostaddr` as conninfo overrides without changing the original database URL or its other parameters (`:101-113`). This retains the DNS name for TLS identity checks while giving libpq a numeric socket destination. The code emits no DSN or credential-bearing diagnostic.

The regression at `tests/unit/test_temporal_schedule_readback.py:534-622` uses the real async plan implementation from each synchronous public wrapper (`describe`, `pause`, and `resume_after` preflight), a DNS hostname paired with loopback `hostaddr`, and a blocking `socket.getaddrinfo` fake. It checks that the resolver is never entered, the caller finishes before resolver release, and neither pause nor local resume mutation occurs. In the pre-fix path, the test's `finally` releases the deliberately blocked resolver so a red run cannot strand the executor thread. The implementation report records red evidence for all three public paths; that reported execution was not rerun during this read-only review.

The supporting tests cover the endpoint contract and failure modes: `_pin()` properties are read before connection close (`:201-253`); the connect spy checks the captured host/address, unchanged URL containing `sslmode=verify-full` and `application_name`, timeout, query parameters, and connection close (`:143-199`); absent or invalid TCP addresses fail before connect and do not expose the URL/password in exception or logs (`:255-282`); and local socket paths omit `hostaddr` while still connecting (`:285-324`). The implementation uses `ipaddress.ip_address()`, which accepts both IPv4 and IPv6 textual addresses.

Endpoint threading is explicit and no mutable instance state is added. `describe()` passes its pin to `_describe()` and its verification/plan read (`:260-271`); `pause()` passes it through both verification reads (`:288-307`); and `resume_after()` uses its first pin for required preflight before `super().resume_after()`, then takes a fresh pin and passes that endpoint through `_resume()` and all of its plan reads (`:310-346`). Thus a preflight failure occurs before the durable local rollback mutation, while the later remote phase uses the endpoint associated with its own second pin.

Existing safety and timeout behavior is preserved in the diff. The plan, preflight, operation, cleanup, Temporal describe, and update timeout constants are unchanged. The close-before-cancel implementation and named task lifecycle are unchanged. The only added connection behavior is the pinned endpoint. Describe retries remain limited to `DEADLINE_EXCEEDED` read-only describes; pause is still called once and is followed by readback; resume still makes one update call, without adding an application retry loop. The tests’ updated private-method fakes accept/pass `endpoint`, including the existing deadline, cancellation, and preflight-fence cases (`tests/unit/test_temporal_schedule_readback.py:327-354`, `:403-481`, `:484-531`, `:624-757`, `:760-794`).

The commit changes only `src/salience/cycles/temporal_schedule_control.py` and `tests/unit/test_temporal_schedule_readback.py`, matching the implementation scope; no unrelated source or evidence files are touched.
