# Service recovery and diagnostics

Sigil retries a lost local service automatically. A running background agent now
counts as active work, so the idle timeout cannot close the service just because
the main agent finished its turn to wait for a worker.

## Recovery behavior

A broken event stream first checks the service state. If the service no longer
answers, the client invokes the existing launcher, which uses the database
ownership lock to start a replacement only when no service owns the database.
A healthy service with a briefly interrupted stream is not relaunched.

The client reloads the endpoint, credentials, state, and transcript after recovery.
A new endpoint resets the event cursor and answered-question identifiers. A new
run on the same port also refreshes its token. Temporary launch failures retain
the retry backoff; repeated background errors no longer fill the transcript or
claim that a typed message was restored when no message was sent.

Automatic relaunch does not preserve in-memory execution. A process that actually
died lost its live tasks; the durable queue and transcript recovery retain their
existing semantics. Preventing idle shutdown while background work is alive is
separate from recovering after an unexpected process death.

## Where to find the logs

Structured records are written under the service directory:

```text
~/.sigil/service/diagnostics/process-<pid>.jsonl
~/.sigil/service/diagnostics/process-<pid>.previous.jsonl
~/.sigil/service/diagnostics/fault-<pid>.log
```

`SIGIL_HOME` and `SIGIL_SERVICE_DIR` change the base location through the existing
path configuration. Files use private `0600` permissions. Each JSON log rotates
at approximately 2 MiB and retains one previous file. Files from different process
ids are retained so a restart does not overwrite the previous process's evidence;
there is no global retention limit across all historical processes.

The JSON records include timestamps, process ids, lifecycle event names, relevant
child/server pids, ports, launch codes, shutdown signals, and exception class and
frame locations. They do not include tokens, passwords, request bodies,
conversation text, exception messages, or traceback local values. Raw process
stdout/stderr remains separate and is not copied into these diagnostics.

When Sigil installs its own Python fault handler, the `fault-<pid>.log` file
captures fatal Python stacks. An already-installed fault handler is preserved.
`SIGKILL`, kernel OOM kills, power loss, and failures before diagnostic startup
cannot produce an in-process traceback. A last startup/ready record without an
exit record helps identify an abrupt loss, but does not prove its cause.

## Investigate a future disconnection

Use timestamps and process ids to correlate the client and server records:

1. Find `client_stream_interrupted` or `client_service_unavailable` in the client
   log, followed by `client_recovery_attempt`.
2. Inspect the previous server's log. `server_idle_shutdown` identifies a planned
   idle stop; `server_signal_received` identifies a shutdown signal;
   `server_process_exception` identifies a propagated failure with frame locations.
3. Check for `server_lifespan_shutdown_started` and
   `server_lifespan_shutdown_finished` to distinguish a completed graceful cleanup
   from one that did not finish.
4. Follow `server_child_spawned`, `server_process_start`, `server_listening`, and
   `client_recovery_endpoint` to the replacement process. A failed launch records
   `client_recovery_failed` and its launch code; a stream that resumes delivering
   events records `client_stream_recovered`.

A disconnect screenshot alone cannot establish why an earlier process stopped.
These records apply to processes running the updated code; they cannot recreate
logs for an earlier incident. Diagnostic write failures do not block startup or
reconnection.
