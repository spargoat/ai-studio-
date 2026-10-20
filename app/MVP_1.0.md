# AI Studio MVP 1.0 checklist (20 Oct 2026)

Built under the rules: no generations, no paid or network AI calls, a backup before each chunk
(`../app.bak-20261020-*`), and the dry-run suite kept passing: `python3 -m unittest tests.test_dryrun`.
The suite has 17 tests and takes about 28 s, against the ~20 s aim.

Status key: **verified** = built and covered by a test or a real local run; **blocked-by** = can't be finished without the named item.

## 1.0 items
| # | Item | State | Evidence |
|---|---|---|---|
| 1 | Character lock in `config/personas.yaml` (face, skin tone, eye colour, hair, build, wardrobe, voice cadence, banned traits) for Maksim, Marco, Callum | verified | test_a1. Drawn only from ref docs, which are listed under `lock.sources`. Unknown fields stay `unknown` and are never injected. Marco's age conflicts between sources (mid/late 20s vs late 30s), so it's flagged CONFLICT and not injected until Aaron confirms. The lock is added to every image and video prompt by `studio/persona.py`. |
| 2 | Asset and prompt library: `studio library list/search/promote/backfill` | verified | test_a2. Uses the `library` table in studio.db. Approved outputs are added automatically when a review is approved and when a job reaches done. The back-fill indexed 22 existing outputs. |
| 3 | Mobile review page in `serve`: versions side by side, score badges, approve/reject (a reason is required to reject), library tab | verified | test_a3. Phone screenshot at `/workspace/ai_studio/review_mobile.png`. Local only (127.0.0.1). |
| 4 | Templates ported from amn-production-os: `static_ad` (Visual Bible → Creative Director → still → headline/CTA compose) and `script_to_scene_beats` (script → beats → keyframe → B-roll) | verified (dry run) | test_a4: both run end to end in dry run. Their LLM stages are **blocked-by: LLM key**. Their image and video stages are **blocked-by: fal top-up** (or a priced Higgsfield fallback). |
| 5 | Script and hook stage: `script` kind with PAS or AIDA framework prompts and 5 hook styles, in the `talking_head_scripted` template | verified | test_a5. Dry run uses mock text marked [MOCK]. A live run with no key stops on the script stage with "needs key". Nothing paid starts, because still and voice wait on the script. **Blocked-by: LLM key** for real copy. |
| 6 | Higgsfield adapter behind the same provider interface, plus routing to it when fal is locked | verified (mocks only) | test_a6. Routing only happens live, when fal is locked and the fallback has a USD price above 0. **Blocked-by:** a Higgsfield API transport and key (only the app and MCP exist today), plus real per-credit USD prices for `hf_*`. Until then live use stops cleanly and nothing is sent. |
| 7 | Compose fixes: exact ElevenLabs word timings for captions when the voice has alignment; bigger default 9:16 overlay (7.5% of the short side, was 5%) | verified (mocked HTTP) | test_a7. Turn on `providers.elevenlabs.with_timestamps: true` so new voices save `alignment.json`. Older voices fall back to script timing. **Not yet exercised live** (needs the next ElevenLabs voice call). |

## Already in place from earlier today
`doctor`, fal lock guard, DB closes, `reconcile`, quality gate (identity and visual scores; copy, brand and engagement shown as "not scored"), and the compose step (captions, overlays, ducked music, 9:16, 1:1 and 16:9).

## What only fal, a key or Aaron unlocks
- **fal top-up:** any live still, video or lipsync; the Maksim LoRA test (~$0.20); the Marco and Callum LoRAs (these also need Aaron to approve Maksim's first); reconciling against the fal dashboard.
- **LLM key** (ANTHROPIC_API_KEY or OPENAI_API_KEY) and a chosen model:
  - real script and hook copy, the Visual Bible, the Creative Director and scene beats;
  - copy, brand and engagement scores;
  - the LLM adapter body (it's a "needs key" stub today).
- **Higgsfield API key and transport, plus USD per credit:** live fallback routing.
- **Aaron:**
  - Callum's face, skin tone, build and voice cadence (unknown in docs);
  - Maksim's skin tone and eye colour (unknown);
  - the identity threshold (0.45 now; 0.6–0.65 likely better).

- 20 Oct: Marco's age set to early to mid 20s (source: Aaron, 20 Oct 2026). Conflict cleared; now in his image and video prompts.
- 20 Oct: Marco's `look` hair changed from "short dark hair" to "dark brown loose wavy curls" to match his locked face sheet (v4).

## Known limits
- The suite takes ~28 s, not ~20 s. Each dry-run job composes 3 aspect ratios.
- The review page header shows the stage's config model, not the per-job override. For example, lipsync shows v2/pro while live-maksim-01 used v3.
- `static_ad` makes one still per run from the first Creative Director prompt. Rerun for more.

## 20 Oct 2026 (10:38 chunk)
- [x] Test suite under 20 s (fast mode exports one aspect; set STUDIO_COMPOSE_ALL=1 for all three aspects, STUDIO_MOCK_FAST=0 for full mode)
- [x] Review header shows the provider and model from the last run record, with a "default" badge when it differs from the template default
- [x] identity_threshold 0.50, tuned on local data (tools/tune_identity.py → data/identity_tuning.csv): wrong faces max 0.374, Maksim min 0.614
- [x] `studio batch --parallel N`; library writes are thread-safe

## 20 Oct 2026 (11:44 chunk)
- [x] 16:9 framing for widescreen, no blur: `zoom` is face-centred with zoom 0–1 and solid-colour sides; `side` places the whole frame at full height on a solid colour, leaving room for text. The talking-head templates now default to zoom 0.6 on 0x141414. Contact sheet: ../wide_16x9_contact.jpg
- [x] `studio export --out DIR [--job/--persona/--promoted/--id N/--live-only/--stages all]` writes media for every aspect, an .srt per aspect (converted from the karaoke ASS), poster.jpg, caption.txt (brief caption/hashtags, falling back to the script) and manifest.json, then zips the bundle. Offline only.
- [x] Tuning note: K6_new_york 02 and 03 contain no person (a bar interior and a tailor's shop); the detector picked up painted faces in them. Both are excluded from the good set.

## 20 Oct 2026 (12:15 chunk): web app
- [x] `studio serve` now serves a React UI built from AMN Production OS frontend code (ui/; built output in ui/dist), backed by a JSON API (studio/api.py) over the existing runner, db, library, export and persona code.
  - Pages: Dashboard, New job, Job detail, Run log, Library, Export, Personas, Spend, Settings.
  - Run it with `cd ui && npm install && npm run build` (only needed after UI changes), then `python3 -m studio serve` and open http://127.0.0.1:8765
  - The UI starts dry runs only. Live runs return a clear blocked message (fal lock or CLI-only). Approving a stage continues the run to the next gate.
  - The old server-rendered pages are still at /classic and /job/<id>.
- [x] Clicked through the journey end to end (dry job → review gates → done → library → export zip). Screenshots: ../ui_shots/
- [ ] Gaps: editing config from Settings; live runs from the UI; push updates (the UI polls); login (local only).

## 20 Oct 2026 (12:33 chunk): hosting behind a password
- [x] App-level shared-password sign-in, built the same way as ads-tracker (APP_PASSWORD + session secret, HMAC-signed 14-day HttpOnly SameSite=Lax cookie, /login page, 401 for the API, 503 if not configured). It's in studio/auth.py and covered by the test_b2 test.
- [x] `studio serve --public` runs on the waitress server, requires sign-in and refuses --live. `host.sh start|stop|status|url` keeps the server and a tunnel running with restart loops. Secrets are in ~/.config/ai_studio/hosting.env (mode 600).
- [ ] Public URL is BLOCKED: the box's outbound network only allows port 443, and Cloudflare quick tunnels need TCP 7844 (QUIC/HTTP2 both time out). Needs Aaron's decision: a free ngrok account authtoken (its agent uses 443), or the box egress opened to 7844.
