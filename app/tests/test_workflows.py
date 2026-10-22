"""Inngest step orchestration. No network, no ffmpeg: providers are stubs and the
function is driven with inngest.experimental.mocked."""
import os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("STUDIO_MOCK_FAST", "1")
os.environ.setdefault("STUDIO_FFMPEG_PRESET", "ultrafast")

import inngest
from inngest.experimental import mocked
from inngest._internal.step_lib.step_sync import StepSync

from studio.config import load_config
from studio.providers.base import NonRetryable, ProviderError
from studio.runner import Runner
from studio.workflows import EVENT_JOB_RUN, QueueError, attach, executor_mode, run_job, send_job


APP = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="studio-inngest-"))


def cfg():
    c = load_config()
    c["_data_dir"] = TMP
    c["_db_file"] = TMP / "studio.db"
    return c


BRIEF = {"id": "t-step", "persona": "marco", "template": "talking_head", "script": "One line."}


class FakeRunner:
    """Records the step order the workflow asks for. No DB, no providers."""

    def __init__(self):
        self.calls = []
        self.polls = 0

    def mark_running(self, job_id):
        self.calls.append("mark")
        return {"status": "running"}

    def stage_ids(self, job_id):
        return ["still", "voice", "video"]

    def decide_stage(self, job_id, stage_id, only=None):
        self.calls.append(f"decide:{stage_id}:{only}")
        if only and stage_id != only:
            return {"action": "skip", "id": stage_id}
        return {"action": "run", "id": stage_id}

    def open_stage(self, job_id, stage_id, reuse_running=True):
        self.calls.append(f"open:{stage_id}")
        return {"state": "open", "kind": "image", "max_try": 3, "run_id": 1, "version": 1,
                "backoff": [0.1, 0.1], "poll": stage_id == "still"}

    def budget_step(self, job_id, stage_id, attempt):
        self.calls.append(f"budget:{stage_id}:{attempt}")
        if stage_id == "video":
            return {"state": "blocked_budget", "error": "cap", "retry": False}
        return {"state": "ok"}

    def attempt_stage(self, job_id, stage_id, attempt, durable=True):
        self.calls.append(f"attempt:{stage_id}:{attempt}")
        if stage_id == "still":
            return {"state": "pending", "retry": True, "poll_seconds": 5, "timeout_seconds": 30,
                    "max_polls": 4, "request_id": "req-1", "backoff": 0.1}
        if stage_id == "voice" and attempt == 1:
            return {"state": "failed", "retry": True, "error": "503", "backoff": 2}
        return {"state": "succeeded", "retry": False}

    def poll_stage(self, job_id, stage_id, attempt):
        self.polls += 1
        self.calls.append(f"poll:{stage_id}:{self.polls}")
        if self.polls < 2:
            return {"state": "pending", "retry": True, "poll_seconds": 5, "timeout_seconds": 30,
                    "max_polls": 4, "request_id": "req-1"}
        return {"state": "succeeded", "retry": False}

    def timeout_attempt(self, job_id, stage_id, attempt):
        self.calls.append(f"timeout:{stage_id}")
        return {"state": "failed", "retry": False, "error": "timeout"}

    def apply_review_gate(self, job_id, stage_id):
        self.calls.append(f"gate:{stage_id}")
        if stage_id == "still":
            return {"action": "waiting"}
        return {"action": "ok"}

    def settle_job(self, job_id, only, failed, waiting, blocked):
        self.calls.append(("settle", list(failed), list(waiting), list(blocked), only))
        if failed:
            return "failed"
        if waiting:
            return "awaiting_review"
        return "done"


class Orchestration(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRunner()
        attach(self.fake)
        self.client = mocked.Inngest(app_id="test")
        self.sleeps = []
        self._orig_sleep = StepSync.sleep

        def wrapped(step, step_id, duration):
            self.sleeps.append(step_id)
            return self._orig_sleep(step, step_id, duration)

        StepSync.sleep = wrapped

    def tearDown(self):
        StepSync.sleep = self._orig_sleep

    def _trigger(self, data):
        return mocked.trigger(
            run_job,
            inngest.Event(name=EVENT_JOB_RUN, data=data),
            self.client,
        )

    def test_steps_poll_retry_backoff_and_budget(self):
        res = self._trigger({"job_id": "job-1"})
        self.assertIs(res.status, mocked.Status.COMPLETED, res.error)
        self.assertEqual(res.output["status"], "failed")          # budget block fails the job
        calls = self.fake.calls
        # fal-style stage: budget, one submit, sleep, poll, sleep, poll, then the gate
        self.assertLess(calls.index("budget:still:1"), calls.index("attempt:still:1"))
        self.assertLess(calls.index("attempt:still:1"), calls.index("poll:still:1"))
        self.assertLess(calls.index("poll:still:1"), calls.index("poll:still:2"))
        self.assertEqual(self.fake.polls, 2)                      # second poll completes; no resubmit
        self.assertEqual(calls.count("attempt:still:1"), 1)
        # retryable provider failure sleeps, then a second attempt, with a fresh budget check
        self.assertLess(calls.index("attempt:voice:1"), calls.index("budget:voice:2"))
        self.assertLess(calls.index("budget:voice:2"), calls.index("attempt:voice:2"))
        # cap check is its own step and it does not call the provider
        self.assertIn("budget:video:1", calls)
        self.assertNotIn("attempt:video:1", calls)
        self.assertIn("p0-still-a1-sleep-0", self.sleeps)
        self.assertIn("p0-still-a1-sleep-1", self.sleeps)
        self.assertIn("p0-voice-backoff-1", self.sleeps)
        settle = calls[-1]
        self.assertEqual(settle[0], "settle")
        self.assertEqual(settle[1], ["video"])                    # failed
        self.assertEqual(settle[2], ["still"])                    # waiting on the review gate
        self.assertEqual(settle[3], [])                           # blocked

    def test_cascade_second_pass_drops_only(self):
        res = self._trigger({"job_id": "job-2", "only": "voice", "cascade": True})
        self.assertIs(res.status, mocked.Status.COMPLETED, res.error)
        decides = [c for c in self.fake.calls if isinstance(c, str) and c.startswith("decide:")]
        self.assertIn("decide:voice:voice", decides)
        self.assertIn("decide:voice:None", decides)               # cascade pass runs the whole job
        self.assertTrue(any(c.startswith("decide:still:") and c.endswith(":None") for c in decides))

    def test_missing_job_id_is_not_retried_as_success(self):
        res = self._trigger({})
        self.assertIs(res.status, mocked.Status.FAILED)


class DurableRunner(unittest.TestCase):
    """Real Runner + DB. The provider is a stub, so nothing is uploaded or rendered."""

    def setUp(self):
        self.runners = []

    def tearDown(self):
        for r in self.runners:
            r.close()

    def _runner(self):
        r = Runner(cfg(), log=lambda m: None)
        self.runners.append(r)
        return r

    def _job(self, r, jid):
        r.create_job(dict(BRIEF, id=jid))
        r.run_stage(r.db.job(jid), next(d for d in r.stage_defs(r.db.job(jid)) if d["id"] == "brief"))

    def test_submit_is_idempotent_and_poll_completes_once(self):
        r = self._runner()
        self._job(r, "t-poll")
        stub = _PollStub()
        r.provider_for = lambda stage, live=None: (stub, r.cfg["stages"][stage])
        opened = r.open_stage("t-poll", "still", reuse_running=False)
        self.assertEqual(opened["state"], "open")
        first = r.attempt_stage("t-poll", "still", 1, durable=True)
        again = r.attempt_stage("t-poll", "still", 1, durable=True)
        self.assertEqual(first["state"], "pending")
        self.assertEqual(again["state"], "pending")
        self.assertEqual(stub.submits, 1)                         # replay must not pay again
        self.assertEqual(r.db.attempts(opened["run_id"])[0]["provider_request_id"], "req-9")
        self.assertEqual(r.poll_stage("t-poll", "still", 1)["state"], "pending")
        done = r.poll_stage("t-poll", "still", 1)
        self.assertEqual(done["state"], "succeeded")
        self.assertEqual(r.poll_stage("t-poll", "still", 1)["state"], "succeeded")
        self.assertEqual(stub.materialized, 1)
        self.assertEqual(r.db.latest("t-poll", "still")["status"], "succeeded")

    def test_sync_run_stage_retries_then_stops_on_nonretryable(self):
        r = self._runner()
        self._job(r, "t-retry")
        flaky = _Flaky()
        r.provider_for = lambda stage, live=None: (flaky, r.cfg["stages"][stage])
        job = r.db.job("t-retry")
        still = next(d for d in r.stage_defs(job) if d["id"] == "still")
        self.assertTrue(r.run_stage(job, still))
        self.assertEqual(r.db.latest("t-retry", "still")["attempts"], 2)
        self.assertEqual(flaky.calls, 2)
        fatal = _Fatal()
        r.provider_for = lambda stage, live=None: (fatal, r.cfg["stages"][stage])
        self.assertFalse(r.run_stage(job, still))
        failed = r.db.latest("t-retry", "still")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["attempts"], 1)
        self.assertIn("needs key", failed["error"])

    def test_budget_step_blocks_before_submit(self):
        r = self._runner()
        self._job(r, "t-budget")
        r.live = True
        r.cfg["budget"] = {"global_cap_usd": 0.0001, "per_job_cap_usd": 0.0001, "daily_cap_usd": None}
        stub = _PollStub()
        stub.paid = True
        r.provider_for = lambda stage, live=None: (stub, r.cfg["stages"][stage])
        r.open_stage("t-budget", "still", reuse_running=False)
        blocked = r.budget_step("t-budget", "still", 1)
        self.assertEqual(blocked["state"], "blocked_budget")
        self.assertEqual(stub.submits, 0)
        self.assertEqual(r.db.latest("t-budget", "still")["status"], "blocked_budget")
        self.assertEqual(r.budget_step("t-budget", "still", 1)["state"], "blocked_budget")


class ExecutorFlag(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("STUDIO_EXECUTOR", None)

    def test_mode_defaults_to_threads(self):
        os.environ.pop("STUDIO_EXECUTOR", None)
        self.assertEqual(executor_mode(), "thread")
        os.environ["STUDIO_EXECUTOR"] = "inline"
        self.assertEqual(executor_mode(), "inline")
        os.environ["STUDIO_EXECUTOR"] = "nope"
        self.assertEqual(executor_mode(), "thread")

    def test_api_sends_event_instead_of_running(self):
        from studio.web import create_app
        from studio import workflows
        os.environ["STUDIO_EXECUTOR"] = "inngest"
        sent = []
        workflows.send_job = lambda job_id, only=None, cascade=False: sent.append((job_id, only, cascade))
        try:
            app = create_app(cfg())
            c = app.test_client()
            res = c.post("/api/jobs", json={"brief": dict(BRIEF, id="t-event")})
            self.assertEqual(res.status_code, 201, res.get_json())
            self.assertEqual(sent, [("t-event", None, False)])
            body = c.get("/api/jobs/t-event").get_json()
            self.assertEqual(body["job"]["status"], "created")        # not left 'running' with no worker
            self.assertFalse(body["running"])
            res = c.post("/api/jobs/t-event/rerun/still")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(sent[-1], ("t-event", "still", False))
        finally:
            workflows.send_job = send_job

    def test_inline_runs_on_the_request(self):
        from studio.web import create_app
        from studio import workflows
        os.environ["STUDIO_EXECUTOR"] = "inline"
        app = create_app(cfg())
        runner = workflows.current_runner()
        called = []
        runner.run_job = lambda jid, only=None: called.append(jid) or runner.db.set_job_status(jid, "awaiting_review") or "awaiting_review"
        res = app.test_client().post("/api/jobs", json={"brief": dict(BRIEF, id="t-inline")})
        self.assertEqual(res.status_code, 201, res.get_json())
        self.assertEqual(called, ["t-inline"])
        self.assertEqual(runner.db.job("t-inline")["status"], "awaiting_review")

    def test_inngest_route_is_not_behind_the_password_gate(self):
        from studio.web import create_app
        os.environ["STUDIO_EXECUTOR"] = "thread"
        keys = ("STUDIO_REQUIRE_AUTH", "STUDIO_APP_PASSWORD", "STUDIO_SESSION_SECRET")
        old = {k: os.environ.get(k) for k in keys}
        try:
            os.environ.update(STUDIO_REQUIRE_AUTH="1", STUDIO_APP_PASSWORD="pw-123456", STUDIO_SESSION_SECRET="secret-abc")
            c = create_app(cfg()).test_client()
            self.assertEqual(c.get("/api/meta").status_code, 401)
            res = c.get("/api/inngest")
            self.assertNotEqual(res.status_code, 404)
            self.assertNotIn(b"Sign in required", res.data)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_send_without_event_key_is_a_queue_error(self):
        # Cloud mode refuses to send when INNGEST_EVENT_KEY is missing. No socket is opened.
        if os.environ.get("INNGEST_EVENT_KEY"):
            self.skipTest("event key is configured")
        from studio import workflows
        old = workflows.client
        workflows.client = inngest.Inngest(app_id="ai-studio-cloud", is_production=True)
        try:
            with self.assertRaises(QueueError):
                workflows.send_job("job-x")
        finally:
            workflows.client = old


class _PollStub:
    name = "fal"
    paid = False
    cfg = {"poll_seconds": 1, "timeout_seconds": 30}

    def __init__(self):
        self.submits = 0
        self.polls = 0
        self.materialized = 0

    def supports_durable_poll(self):
        return True

    def submit(self, kind, ctx):
        self.submits += 1
        return {"request_id": "req-9", "status_url": "https://example.test/status",
                "response_url": "https://example.test/result", "poll_seconds": 1,
                "timeout_seconds": 30, "kind": kind, "mode": "images"}

    def poll(self, ticket):
        self.polls += 1
        if self.polls < 2:
            return {"state": "pending", "provider_status": "IN_PROGRESS"}
        return {"state": "completed", "payload": {"images": [{"url": "https://example.test/still.png"}]}}

    def materialize(self, kind, ctx, request_id, payload, mode=None):
        self.materialized += 1
        out = Path(ctx["out_dir"]) / "still.png"
        out.write_bytes(b"\x89PNG" + b"x" * 32)
        return {"output_path": str(out), "meta": {"url": payload["images"][0]["url"]},
                "units_actual": 1, "request_id": request_id}

    def request_urls(self, model, rid):
        return "https://example.test/status", "https://example.test/result"


class _Flaky:
    name = "mock"
    paid = False

    def __init__(self):
        self.calls = 0

    def supports_durable_poll(self):
        return False

    def run(self, kind, ctx):
        self.calls += 1
        if self.calls < 2:
            raise ProviderError("503 simulated")
        out = Path(ctx["out_dir"]) / "still.png"
        out.write_bytes(b"\x89PNG" + b"x" * 32)
        return {"output_path": str(out), "meta": {}, "units_actual": 1, "request_id": "m"}


class _Fatal:
    name = "llm"
    paid = True

    def supports_durable_poll(self):
        return False

    def run(self, kind, ctx):
        raise NonRetryable("needs key: LLM stage needs one of ['ANTHROPIC_API_KEY'] (none set). Nothing was called.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
