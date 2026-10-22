# AI Studio

Talking-head stage runner (Flask) and React UI, hosted on Vercel behind a shared password.

Source lives in [`app/`](app/). Engine commands and templates are in [app/README.md](app/README.md).

## Hosting

| Piece | Where |
| --- | --- |
| API, login, `/file`, classic pages, `/api/inngest` | Flask app `app/wsgi.py` (one Python function) |
| Hashed UI assets | `public/assets`, copied from `app/ui/dist` at build. Vercel serves that directory before the function. Flask also serves `app/ui/dist/assets` if a request still reaches it |
| Jobs database | Turso/libSQL when `TURSO_DATABASE_URL` is set. Local SQLite otherwise. On Vercel, with no URL and without `STUDIO_SQLITE=1`, routes return 503 `Database not configured` except `/healthz`, `/assets/*`, and `/api/inngest` |
| Job files | Vercel Blob when `BLOB_READ_WRITE_TOKEN` is set. A stage pulls inputs into `/tmp`, runs, uploads outputs, and stores the Blob URL |
| Job execution | `STUDIO_EXECUTOR=inngest` sends `studio/job.run`. Inngest calls `/api/inngest`, which is not behind the studio password (Inngest signs the request). Without that flag, Vercel runs one stage per `POST /api/jobs/<id>/advance` or cron `GET /api/cron/advance` |
| ffmpeg and face models | Not in the function bundle. On first use they download into `/tmp` (`/tmp/studio-bin`, `/tmp/studio-models`), from Blob `runtime/<name>` when that object exists, otherwise from the public source URL |

Vercel project root is this repository (not `app/`). Build command is in `vercel.json`. It builds the UI and copies hashed assets. It does not download ffmpeg or the face models.

## Environment variables

Set these in the Vercel project. Never commit values.

### Required to boot the hosted app

| Name | Purpose |
| --- | --- |
| `STUDIO_APP_PASSWORD` | Shared password |
| `STUDIO_SESSION_SECRET` | HMAC secret for the session cookie |
| `STUDIO_REQUIRE_AUTH` | Set to `1` so the app fails closed when the password or secret is missing |
| `TURSO_DATABASE_URL` | libSQL URL (`libsql://...`). `LIBSQL_URL` is accepted as an alias |
| `TURSO_AUTH_TOKEN` | Turso token. `LIBSQL_AUTH_TOKEN` is accepted as an alias |
| `BLOB_READ_WRITE_TOKEN` | Vercel Blob read/write token |
| `CRON_SECRET` | Bearer token Vercel sends to `/api/cron/advance` |
| `INNGEST_EVENT_KEY` | Sends `studio/job.run` when `STUDIO_EXECUTOR=inngest`. The Inngest Vercel integration writes this |
| `INNGEST_SIGNING_KEY` | Verifies Inngest's calls to `/api/inngest`. The integration writes this |
| `STUDIO_EXECUTOR` | Set to `inngest` so creates, resumes, and reruns send an event instead of a thread |

### Required only for live provider calls

| Name | Purpose |
| --- | --- |
| `STUDIO_ALLOW_LIVE` | Set to `1` or the API keeps returning 409 for live jobs |
| `STUDIO_LIVE` | Set to `1` to construct the process runner in live mode |
| `FAL_KEY` | fal.ai |
| `ELEVENLABS_API_KEY` | ElevenLabs |
| `HIGGSFIELD_API_KEY` | Higgsfield fallback. `HIGGSFIELD_API_SECRET` or `HF_KEY` also work |
| `STUDIO_FAL_TOPPED_UP` | Set to `1` after topping up a locked fal account |

### Optional

| Name | Purpose |
| --- | --- |
| `INNGEST_DEV` | Set to `1` locally to use the Inngest Dev Server. Leave unset on Vercel |
| `STUDIO_SQLITE` | Set to `1` to force the local SQLite file on Vercel |
| `STUDIO_FETCH_FFMPEG` | Set to `1` to download the static ffmpeg outside Vercel. On Vercel this happens when `VERCEL=1` |

## Bundle

The Python builder packs the function to about 225 MB uncompressed (`LAMBDA_SIZE_EXCEEDED` at 236.69 MB when ffmpeg and the face models were copied into `app/bin` and `app/models` during the build). Those files are excluded (`app/models/**`, `app/bin/**`) and fetched at runtime. opencv-python-headless is not a request-function dependency. Identity scoring uses onnxruntime when OpenCV is absent.

`python3 scripts/measure_bundle.py` reprints the uncompressed size. On Python 3.12 that measurement is **167.7 MB** including the built UI (onnxruntime 61.5 MB, numpy about 54 MB, libsql 12.4 MB, Pillow about 19 MB). The script exits non-zero above 180 MB.

## Local checks

```bash
pip install -r requirements.txt '.[score]'
cd app/ui && npm ci && npm run build && cd ../..
cd app
python3 -m unittest tests.test_dryrun tests.test_workflows tests.test_higgsfield tests.test_still_provider tests.test_storage tests.test_advance tests.test_assets tests.test_static tests.test_fonts tests.test_refs tests.test_tmp
```

## Dashboard

1. Project **balconyvulcan**, team **twf-jordan**. Framework preset Flask. Root directory: the repository root (clear a root of `app` if one is set).
2. From the Marketplace, add Turso, Vercel Blob, and the Inngest integration.
3. Set `STUDIO_APP_PASSWORD`, `STUDIO_SESSION_SECRET`, `STUDIO_REQUIRE_AUTH=1`, `CRON_SECRET`, and `STUDIO_EXECUTOR=inngest`.
4. Cron `* * * * *` needs a Pro plan. Hobby crons run at most once a day.
