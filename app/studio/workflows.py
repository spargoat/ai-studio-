"""Durable job execution on Inngest.

`db.py` stays the record of job and stage state. Inngest only sequences the
work: each stage, each budget-cap check, each retry, and each provider poll is
its own step. A provider that can poll (fal) is submitted once; the function
then `step.sleep`s and checks again, so a Vercel timeout cannot leave the job
stuck at 'running' inside a poll loop.

`STUDIO_EXECUTOR` selects how the API starts a job:

- `thread` (default) — background thread. Local `studio serve` and the dry-run tests.
- `inline` — run on the request thread. Tests that must not spawn threads.
- `inngest` — send `studio/job.run` and return. Inngest calls `/api/inngest`.

Production needs `INNGEST_EVENT_KEY` and `INNGEST_SIGNING_KEY`. Set `INNGEST_DEV=1`
to talk to the local Dev Server instead of Inngest Cloud.
"""
import datetime
import logging
import os

import inngest

EVENT_JOB_RUN = "studio/job.run"
_MODES = ("thread", "inline", "inngest")

log = logging.getLogger("studio.inngest")


def _make_client():
    """Cloud mode only when a signing key is present.

    `studio serve` and the dry-run tests have no key. Dev mode lets the
    /api/inngest route mount anyway. On Vercel the integration injects
    INNGEST_SIGNING_KEY before the process starts, and the endpoint then
    rejects unsigned calls. INNGEST_DEV=1 (or a Dev Server URL) always
    selects the Dev Server, including its origin.
    """
    if os.getenv("INNGEST_DEV"):
        return inngest.Inngest(app_id="ai-studio", logger=log)
    return inngest.Inngest(
        app_id="ai-studio",
        logger=log,
        is_production=bool(os.getenv("INNGEST_SIGNING_KEY")),
    )


client = _make_client()

_runner = None


class QueueError(Exception):
    """The job was not handed to Inngest. The DB row is unchanged by the send itself."""


def executor_mode():
    mode = (os.environ.get("STUDIO_EXECUTOR") or "thread").strip().lower()
    return mode if mode in _MODES else "thread"


def attach(runner):
    """Bind the process-wide Runner. create_app calls this before serving."""
    global _runner
    _runner = runner


def current_runner():
    if _runner is None:
        raise QueueError("studio runner is not attached")
    return _runner


def send_job(job_id, only=None, cascade=False):
    """Ask Inngest to run (or resume) a job. Raises QueueError if the event is not accepted."""
    try:
        client.send_sync(inngest.Event(
            name=EVENT_JOB_RUN,
            data={"job_id": job_id, "only": only, "cascade": bool(cascade)},
        ))
    except Exception as e:
        raise QueueError(f"could not send {EVENT_JOB_RUN} for {job_id}: {e}") from e


def _sleep(seconds):
    return datetime.timedelta(seconds=max(0.0, float(seconds or 0)))


def _drive_stage(ctx, runner, job_id, sid, prefix):
    """One stage: open, then budget / attempt / poll / backoff as separate steps."""
    opened = ctx.step.run(
        f"{prefix}{sid}-open",
        lambda sid=sid: runner.open_stage(job_id, sid, reuse_running=True),
    )
    if opened.get("kind") == "brief":
        return opened.get("state") == "succeeded"
    max_try = int(opened.get("max_try") or 1)
    for attempt in range(1, max_try + 1):
        budget = ctx.step.run(
            f"{prefix}{sid}-budget-{attempt}",
            lambda sid=sid, attempt=attempt: runner.budget_step(job_id, sid, attempt),
        )
        if budget.get("state") == "blocked_budget":
            return False
        outcome = ctx.step.run(
            f"{prefix}{sid}-attempt-{attempt}",
            lambda sid=sid, attempt=attempt: runner.attempt_stage(job_id, sid, attempt, durable=True),
        )
        guard = 0
        while outcome.get("state") == "pending":
            max_polls = int(outcome.get("max_polls") or 1)
            if guard >= max_polls:
                outcome = ctx.step.run(
                    f"{prefix}{sid}-a{attempt}-timeout",
                    lambda sid=sid, attempt=attempt: runner.timeout_attempt(job_id, sid, attempt),
                )
                break
            ctx.step.sleep(
                f"{prefix}{sid}-a{attempt}-sleep-{guard}",
                _sleep(outcome.get("poll_seconds")),
            )
            outcome = ctx.step.run(
                f"{prefix}{sid}-a{attempt}-poll-{guard}",
                lambda sid=sid, attempt=attempt: runner.poll_stage(job_id, sid, attempt),
            )
            guard += 1
        if outcome.get("state") == "succeeded":
            return True
        if not outcome.get("retry"):
            return False
        if attempt < max_try:
            ctx.step.sleep(
                f"{prefix}{sid}-backoff-{attempt}",
                _sleep(outcome.get("backoff")),
            )
    return False


def _pass(ctx, runner, job_id, only, prefix):
    ctx.step.run(f"{prefix}mark-running", lambda: runner.mark_running(job_id))
    ids = ctx.step.run(f"{prefix}stages", lambda: list(runner.stage_ids(job_id)))
    failed, waiting, blocked = [], [], []
    for sid in ids:
        decision = ctx.step.run(
            f"{prefix}{sid}-decide",
            lambda sid=sid: runner.decide_stage(job_id, sid, only),
        )
        action = decision.get("action")
        if action == "skip":
            continue
        if action == "blocked":
            blocked.append(sid)
            continue
        if action == "waiting":
            waiting.append(sid)
            continue
        if action == "run":
            if not _drive_stage(ctx, runner, job_id, sid, prefix):
                failed.append(sid)
                continue
        if action in ("run", "gate"):
            gate = ctx.step.run(
                f"{prefix}{sid}-gate",
                lambda sid=sid: runner.apply_review_gate(job_id, sid),
            )
            if gate.get("action") == "waiting":
                waiting.append(sid)
    return ctx.step.run(
        f"{prefix}settle",
        lambda: runner.settle_job(job_id, only, list(failed), list(waiting), list(blocked)),
    )


@client.create_function(
    fn_id="studio-job-run",
    name="Studio job run",
    trigger=inngest.TriggerEvent(event=EVENT_JOB_RUN),
    concurrency=[inngest.Concurrency(limit=1, key="event.data.job_id")],
    retries=3,
)
def run_job(ctx: inngest.ContextSync) -> dict:
    """Run every due stage of one job. State lives in the studio DB, not in Inngest."""
    data = ctx.event.data or {}
    job_id = data.get("job_id")
    if not job_id:
        raise inngest.NonRetriableError("studio/job.run requires data.job_id")
    only = data.get("only") or None
    cascade = bool(data.get("cascade"))
    runner = current_runner()
    status = _pass(ctx, runner, job_id, only, "p0-")
    if cascade and only and status != "failed":
        status = _pass(ctx, runner, job_id, None, "p1-")
    return {"job_id": job_id, "status": status}


def serve(app):
    """Mount the Inngest endpoint on the Flask app (GET sync, PUT register, POST invoke)."""
    import inngest.flask
    inngest.flask.serve(app, client, [run_job])
