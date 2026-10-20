# AI Studio

Talking-head stage runner (Flask) and React UI, hosted on Vercel behind a shared password.
The engine and the UI are unchanged: Python serves `/api`, and the Vite build in `app/ui/dist` is static.

Source lives in [`app/`](app/). Engine commands and templates are in [app/README.md](app/README.md).

## Hosting

| Piece | Where |
| --- | --- |
| API, login, `/file`, classic pages | Flask app `app/wsgi.py` (one Python function) |
| Hashed UI assets | `public/assets`, copied from `app/ui/dist` at build. Vercel serves that directory before the function, so `/assets/*` does not go through Flask or the password gate. Flask also serves `app/ui/dist/assets` if a request still reaches it. The build fails when `index.html` references a file that was not copied |
| Jobs database | Turso/libSQL when `TURSO_DATABASE_URL` is set. Local SQLite otherwise. On Vercel, with no URL and without `STUDIO_SQLITE=1`, the app does not open `/tmp` SQLite: app and `/api` routes return 503 `Database not configured`, and `/api/cron/advance` does not write |
| Job files | Vercel Blob when `BLOB_READ_WRITE_TOKEN` is set. Uploads are `PUT https://vercel.com/api/blob/?pathname=` with `x-api-version: 13`, the store id from the token, `x-content-type`, `x-add-random-suffix: 0`, and `x-allow-overwrite: 1` (the `@vercel/blob` 2.8.2 protocol). A rejected upload is logged with the response body, the stage run and the job are marked `failed`, and advance returns that error in JSON. A stage pulls inputs into `/tmp`, runs, uploads outputs, and stores the Blob URL. `/file` redirects only to `*.blob.vercel-storage.com` |
| Progress | `POST /api/jobs/<id>/advance` runs one stage, or one fal queue poll. Cron `GET /api/cron/advance` moves jobs in `created`, `running`, or `partial`. A stage that failed or hit the spend guard marks the job `failed`; further advance and cron calls return that error and do not start another version. Re-run is the explicit retry |
| Captions | DejaVu Sans and DejaVu Sans Bold in `app/fonts` (Bitstream Vera license, see `app/fonts/LICENSE`). drawtext gets `fontfile=`; libass captions get `fontsdir` pointing at that directory. A template `fonts_dir` is used only when that directory exists |
| Face score | Optional. Local installs use `pip install '.[score]'` (numpy + opencv-python-headless). The request function does not include them. YuNet + SFace (`yunet.onnx`, `sface.onnx`) download into `/tmp` on first score, from Blob `runtime/<name>` when that object exists, otherwise from the [OpenCV model zoo](https://github.com/opencv/opencv_zoo), then the bytes are stored in Blob |
| ffmpeg | Not in the bundle. On Vercel, the first compose/finish downloads a static Linux build into `/tmp` (Blob `runtime/ffmpeg-linux64.tar.xz` when present, otherwise the BtbN release) and unpacks `ffmpeg` and `ffprobe` |

Live runs from the app return **409** unless `STUDIO_ALLOW_LIVE=1`. Dry runs stay the default.

Vercel project root is this repository (not `app/`). Build command is in `vercel.json`.

## Environment variables

Set these in the Vercel project. Never commit values. Names only:

### Required to boot the hosted app

| Name | Purpose |
| --- | --- |
| `STUDIO_APP_PASSWORD` | Shared password (`studio/auth.py`) |
| `STUDIO_SESSION_SECRET` | HMAC secret for the session cookie |
| `STUDIO_REQUIRE_AUTH` | Set to `1` so the app fails closed when the password or secret is missing |
| `TURSO_DATABASE_URL` | libSQL URL (`libsql://...`). `LIBSQL_URL` is accepted as an alias |
| `TURSO_AUTH_TOKEN` | Turso token. `LIBSQL_AUTH_TOKEN` is accepted as an alias |
| `BLOB_READ_WRITE_TOKEN` | Vercel Blob read/write token |
| `CRON_SECRET` | Bearer token Vercel sends to `/api/cron/advance`. The route rejects calls without it |

### Required only for live provider calls

Leave unset until a live run is intended. Caps in `app/config/config.yaml` still apply.

| Name | Purpose |
| --- | --- |
| `STUDIO_ALLOW_LIVE` | Set to `1` or the API keeps returning 409 for live jobs |
| `FAL_KEY` | fal.ai |
| `ELEVENLABS_API_KEY` | ElevenLabs (`ELEVENLABS` is an alias) |
| `ANTHROPIC_API_KEY` | Optional LLM stages. `OPENAI_API_KEY` is the other accepted name |
| `HIGGSFIELD_API_KEY` | Optional fallback provider |
| `STUDIO_FAL_TOPPED_UP` | Set to `1` after topping up a locked fal account |

### Optional

| Name | Purpose |
| --- | --- |
| `STUDIO_COOKIE_SECURE` | Set to `1` to mark the cookie Secure even when the proxy header is absent. HTTPS on Vercel sets this from `X-Forwarded-Proto` |
| `VERCEL_SUPPORT_LARGE_FUNCTIONS` | Set to `1` only if the Python bundle exceeds the 500 MB standard limit |
| `STUDIO_SQLITE` | Set to `1` to force the local SQLite file. On Vercel this is a testing override: without it, a missing Turso URL is a 503, not an ephemeral database. Also forces SQLite when a Turso URL is present |
| `STUDIO_FETCH_FFMPEG` | Set to `1` to download the static ffmpeg outside Vercel. On Vercel this happens when `VERCEL=1` |
| `ADINTEL_API_URL` | Base URL of the ads-tracker read API. With `ADINTEL_API_SECRET`, Intel nodes call it. If either is unset, the board uses a labelled fixture and does not present it as live |
| `ADINTEL_API_SECRET` | Shared server-side secret for that read API. Sent as a bearer token. Never returned to the browser |

`VERCEL` is set by the platform. Do not copy it into a local `.env`.

## Local checks

```bash
# from the repository root; the score extra is local only
pip install -r requirements.txt '.[score]'
cd app
python3 -m unittest tests.test_dryrun tests.test_storage tests.test_advance tests.test_assets tests.test_static tests.test_fonts
```

Dry-run tests use a throwaway SQLite file and mock providers. They do not call fal or ElevenLabs. The `score` extra is what the quality-gate tests import; it is not installed into the Vercel function.

## What Aaron still does in the dashboard

1. Import this repo into Vercel team **twf-jordan**, project **ai-studio**. Framework: Flask. Root directory: the repository root.
2. From the Marketplace, add **Turso** and **Vercel Blob** on that project so the env vars above are created. Do not paste secrets into git.
3. Set `STUDIO_APP_PASSWORD`, `STUDIO_SESSION_SECRET`, `STUDIO_REQUIRE_AUTH=1`, and `CRON_SECRET`.
4. Leave `STUDIO_ALLOW_LIVE` unset until a paid run is deliberate.
5. Cron is `* * * * *` (every minute). That schedule needs a Pro plan; Hobby crons run at most once a day. Do not promote the deployment to production from this change.

## Limits

- One Python function. Default max duration is **300 seconds** (Hobby maximum). A single stage that runs longer than that, usually compose/finish on a long clip, returns 504. Pro can raise `maxDuration` up to 800s in `vercel.json`.
- Request bodies are capped at **4.5 MB**. Job media is uploaded to Blob, not through the function body.
- This project's Python builder packs the function to about **225 MB** uncompressed (`LAMBDA_SIZE_EXCEEDED` at 236.69 MB, 405.79 MB before that pack). opencv-python-headless and numpy are not dependencies of the request function. Face models and ffmpeg are fetched into `/tmp` at runtime, and `excludeFiles` drops tests, UI source, `node_modules`, screenshots, and `app/models`. The hashed UI files stay in the function (`includeFiles`: `app/ui/dist/**`) so Flask can serve them, and the same bytes are copied to `public/assets` for the CDN. `python3 scripts/measure_bundle.py` reprints that uncompressed size (pip wheels plus the project files the builder would zip). Measured on Python 3.12: **22.0 MB** (19.4 MB of wheels, 2.5 MB of source, including the Vite assets and the DejaVu faces). The builder also adds its small runtime shim; that is not in the 22.0 MB.
- On Vercel, identity and visual scores are not computed, and compose framing uses the geometric center instead of a detected face. The stage still finishes; the quality gate only flags, it does not block. Locally, with the `score` extra installed, scoring and face framing are unchanged.
- The first compose or finish on a cold instance downloads the ffmpeg archive (about 150 MB) before it encodes. Later requests on that instance reuse `/tmp`. A new instance downloads again, from Blob when `runtime/ffmpeg-linux64.tar.xz` was stored.
- fal generations do not block the request. Advance submits, stores the request id, and the next advance or the cron polls once.
- Blob objects are public unguessable URLs so `<img>` and `<video>` can load them after `/file` redirects. The password gates the app and the `/file` route, not a URL someone already has. The same store holds the runtime ffmpeg and model cache under `runtime/`.
