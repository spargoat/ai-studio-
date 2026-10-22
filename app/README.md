# AI Studio M1: talking-head stage-runner (AAR-72)

DRY_RUN is the default (mock providers make placeholder media, $0). Real calls need `--live` AND
`budget.global_cap_usd` + `budget.per_job_cap_usd` > 0 in `config/config.yaml`. Keys are read by env
name only (FAL_KEY, ELEVENLABS_API_KEY); values are never printed.

Stages (templates/talking_head.yaml): brief > still [review gate] > voice [review gate] > video > lipsync > finish.
Deps: video needs an approved still; lipsync needs video + approved voice. Voice is scheduled before video so
Kling's duration matches the real audio.

    python3 -m studio estimate briefs/example_marco.yaml          # cost per stage, no calls
    python3 -m studio new briefs/example_marco.yaml               # dry run, stops at review gates
    python3 -m studio approve JOB still --note "ok"               # or reject --note "reason"
    python3 -m studio run JOB                                     # resume: runs due/stale stages
    python3 -m studio rerun JOB --stage lipsync [--cascade]       # rerun one stage only (new version)
    python3 -m studio batch briefs/batch3.csv                     # N jobs from CSV/YAML
    python3 -m studio status [JOB] | keys | save-template JOB NAME
    python3 -m studio serve --port 8765                           # review page http://127.0.0.1:8765
    python3 -m studio --live --auto-check new briefs/live_maksim_01.yaml   # REAL spend, capped
    python3 tests/test_dryrun.py                                  # dry-run tests (paid adapters tripwired)

Flags: `--auto-approve` (dry only), `--auto-check` (gate passes only if blank/corrupt QC passes),
`--fail-stage lipsync[:N]` (mock-only failure injection).

Layout: studio/ (runner, db, pricing, qc, web, cli, providers/{mock,fal,elevenlabs,ffmpeg_local}),
config/ (config.yaml with stage adapters + dated price table, personas.yaml with locked voices/refs),
templates/, briefs/, refs/maksim/, data/studio.db + data/jobs/<job>/<stage>/vN/, screens/, tests/.

## Overrides and template defaults (updated 19 Oct 2026)

    python3 -m studio override JOB lipsync --model fal-ai/sync-lipsync/v3 --price-key fal_sync3_lipsync --param audio_lead_in_s=0.3
    python3 -m studio override JOB video --prompt "..."            # then: rerun JOB --stage video --cascade

talking_head template v2 defaults:
- lipsync uses sync-3 (`fal-ai/sync-lipsync/v3`, $0.1333/s) with a 0.3 s silent audio lead-in, padded locally.
  On live-maksim-01 it beat lipsync-2-pro on mouth/audio correlation (0.47 vs 0.26) and first-4 s onset (0.89 vs 0.44).
  The cost is a softer lower face, partly offset by the finish sharpen. lipsync-2-pro is still the documented fallback.
- The video prompt describes talking motion: "speaking calmly…". The old prompt said "mouth movement neutral".
- finish uses the `hf_warm` grade (contrast, warmth, light sharpen), light grain, loudnorm -16 LUFS and a highpass.
  It writes `final.mp4` (crf 19 master) plus `final_send.mp4`, which is sized under `send_mb` (18 MB) for chat/email.
- Lip-sync and motion QC: `tools/lipqc.py name=clip.mp4 ...` (mediapipe) prints mouth_env_corr, onset_ratio,
  blinks/min, sway and beard/upper sharpness.

## Persona LoRAs

The registry lives in two files:
- `config/personas.yaml`: per persona, a `lora:` block with the trigger word, the dataset sources (locked assets with tags),
  holdout images and identity_refs.
- `config/loras.yaml`: written by training. It holds the versions and the active version, with high- and low-noise URLs,
  steps, dataset hash, cost, request id and status.

    python3 -m studio --live train-lora maksim --steps 1000   # builds the dataset, uploads it, trains (budget-guarded)
    python3 -m studio --live lora-test maksim --n 4            # 4 test stills -> data/loras/maksim/vN/test/
    python3 -m studio loras                                    # show the registry

- Trainer: `fal-ai/wan-22-image-trainer` at $0.0045/step, so 1000 steps is $4.50.
  Settings: face detection and masks on, learning rate 7e-4, and a trigger phrase such as "mksmptrv man".
  It outputs a high-noise and a low-noise LoRA (Wan 2.2 A14B MoE).
- `build_dataset` resizes images to 1536 px or less and writes NNN.jpg plus NNN.txt captions.
  Captions read "photo of {trigger}, " followed by the source tags (framing, angle, setting).
  Identity traits are deliberately left out of the captions so they bind to the trigger.
  The dataset zip and manifest sit in data/loras/{persona}/vN/.
- Using a LoRA in a job: set `use_lora: true` in the still or video stage params. You can do this with
  `override JOB still --param use_lora=true` or in a brief. The runner then switches to
  `stages.<stage>.lora_route` (Wan 2.2 T2I LoRA $0.05/image; Wan 2.2 I2V LoRA $0.10/s, 720p) and prefixes the trigger.
- Nano Banana 2 edit has no LoRA input, so it stays the reference-image still route.
- Train Marco and Callum only after Aaron reviews the Maksim result. Their personas.yaml blocks are prepared,
  status "not trained".

## Health check (offline, free)
`python3 -m studio doctor` checks key names (never values), caps, ledger spend, failed live attempts, fal lock state, stuck runs, pending reviews and LoRAs. It exits 1 if live mode would be refused.
If fal's last response was "User is locked / Exhausted balance", `--live` is refused until you top up and run with `STUDIO_FAL_TOPPED_UP=1`.
Tests: `python3 -m unittest tests.test_dryrun` (they set STUDIO_MOCK_FAST=1 and STUDIO_FFMPEG_PRESET=ultrafast).

## Durable jobs on Vercel (Inngest)

The API used to run each job on a background thread. The job only moved while that process was still alive, because the stage loop lived inside the request. On Vercel the function freezes or hits its max duration, the thread dies, and the row stays `running`.

`studio/workflows.py` runs the same stages as an Inngest function, served by the Flask app at `/api/inngest`. `studio/db.py` is still the record of job and stage state. Inngest only sequences steps:

- each stage is its own `step.run` (`open`, then the attempt)
- a budget-cap check is its own `step.run` and, on a miss, no provider call is made
- retries sleep with `step.sleep` for the configured backoff, then start the next attempt
- fal (any provider with `supports_durable_poll`) is submitted once; the function then `step.sleep`s and checks status. A timeout resumes from the saved provider request id and does not submit again

One Inngest run is limited to a single job (`concurrency` key `event.data.job_id`).

### Executor

`STUDIO_EXECUTOR` chooses how `POST /api/jobs`, resume, and rerun start work. The classic review page uses the same switch.

| value | behaviour |
| --- | --- |
| `thread` (default) | background thread, same as a local `studio serve`. Dry-run tests leave this unset. |
| `inline` | run on the request thread |
| `inngest` | send `studio/job.run` and return. The job stays `created` until Inngest calls back. |

The CLI (`studio new`, `studio run`, `studio rerun`) still calls `Runner.run_job` in-process. That path is the right one on a machine you keep open.

### Environment

| variable | required | purpose |
| --- | --- | --- |
| `INNGEST_EVENT_KEY` | yes, when `STUDIO_EXECUTOR=inngest` in production | lets the app send `studio/job.run`. The SDK reads it. Without it, the API returns 503 and the job is left `created`, not `running`. |
| `INNGEST_SIGNING_KEY` | yes, in production | verifies Inngest's calls to `/api/inngest`. The SDK rejects unsigned invocations when this is set and the app is in cloud mode. |
| `INNGEST_DEV` | local only | set to `1` (or the Dev Server URL) to use the Dev Server instead of Inngest Cloud. Leave it unset on Vercel. |
| `STUDIO_EXECUTOR` | production | `inngest` on Vercel. `thread` or unset locally. |

Install the SDK with the app (`requirements.txt` includes `inngest>=0.5.14`).

### Vercel

The project root for the Python app is `app/` (`app.py` builds the Flask app, `requirements.txt` installs dependencies).

1. In the Vercel project, set the root directory to `app` if it is not already.
2. Install the [Inngest integration](https://www.inngest.com/docs/deploy/vercel) from the Vercel Marketplace. It writes `INNGEST_EVENT_KEY` and `INNGEST_SIGNING_KEY` and syncs the app after each deploy so `/api/inngest` is registered.
3. Set `STUDIO_EXECUTOR=inngest` on the Vercel project (Production, and Preview if those deployments should run jobs).
4. Do not put the studio password in front of `/api/inngest`. The route is exempt from the sign-in gate; Inngest signs the request with `INNGEST_SIGNING_KEY`.
5. Raise the function max duration high enough for one step (a submit, a single status check, or a local encode), not for a whole fal wait. The wait is a `step.sleep` between invocations. On the hobby plan that ceiling is 60 seconds; a long ffmpeg step still has to finish inside it.
6. The SQLite file from `db_file` in `config/config.yaml` is the source of truth. Serverless disks are ephemeral, so that file has to live on storage that survives the next invocation (the same data directory the app already uses). Inngest does not keep a second copy of job state.

Local sync against the Dev Server:

```
INNGEST_DEV=1 STUDIO_EXECUTOR=inngest python3 -m studio serve --port 8765
npx inngest-cli@latest dev -u http://127.0.0.1:8765/api/inngest
```

Step orchestration tests (no Dev Server, no network): `python3 -m unittest tests.test_workflows`.
