# AI Studio

Talking-head stage runner (Flask) and React UI, hosted on Vercel behind a shared password.
The engine and the UI are unchanged: Python serves `/api`, and the Vite build in `app/ui/dist` is static.

Source lives in [`app/`](app/). Engine commands and templates are in [app/README.md](app/README.md).

## Hosting

| Piece | Where |
| --- | --- |
| API, login, `/file`, classic pages | Flask app `app/wsgi.py` (one Python function) |
| Hashed UI assets | `public/assets`, copied from `app/ui/dist` at build |
| Jobs database | Turso/libSQL when `TURSO_DATABASE_URL` is set; local SQLite otherwise (tests and CLI) |
| Job files | Vercel Blob when `BLOB_READ_WRITE_TOKEN` is set. A stage pulls inputs into `/tmp`, runs, uploads outputs, and stores the Blob URL. `/file` redirects only to `*.blob.vercel-storage.com` |
| Progress | `POST /api/jobs/<id>/advance` runs one stage, or one fal queue poll. Cron `GET /api/cron/advance` moves jobs in `created`, `running`, or `partial` |
| Face score | OpenCV YuNet + SFace (`yunet.onnx`, `sface.onnx`) from the [OpenCV model zoo](https://github.com/opencv/opencv_zoo), downloaded at build |
| ffmpeg | Static Linux build unpacked to `/tmp` on first compose/finish |

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
| `STUDIO_SQLITE` | Set to `1` to force the local SQLite file even if a Turso URL is present (tests) |

`VERCEL` is set by the platform. Do not copy it into a local `.env`.

## Local checks

```bash
cd app
python3 -m unittest tests.test_dryrun tests.test_storage tests.test_advance
```

Dry-run tests use a throwaway SQLite file and mock providers. They do not call fal or ElevenLabs.

## What Aaron still does in the dashboard

1. Import this repo into Vercel team **twf-jordan**, project **ai-studio**. Framework: Flask. Root directory: the repository root.
2. From the Marketplace, add **Turso** and **Vercel Blob** on that project so the env vars above are created. Do not paste secrets into git.
3. Set `STUDIO_APP_PASSWORD`, `STUDIO_SESSION_SECRET`, `STUDIO_REQUIRE_AUTH=1`, and `CRON_SECRET`.
4. Leave `STUDIO_ALLOW_LIVE` unset until a paid run is deliberate.
5. Cron is `* * * * *` (every minute). That schedule needs a Pro plan; Hobby crons run at most once a day. Do not promote the deployment to production from this change.

## Limits

- One Python function. Default max duration is **300 seconds** (Hobby maximum). A single stage that runs longer than that, usually compose/finish on a long clip, returns 504. Pro can raise `maxDuration` up to 800s in `vercel.json`.
- Request bodies are capped at **4.5 MB**. Job media is uploaded to Blob, not through the function body.
- The standard Python bundle limit is **500 MB** uncompressed. opencv-python-headless, the two face models (~38 MB), and the compressed static ffmpeg (~150 MB) are expected to fit. If a build reports the unzipped-size error, set `VERCEL_SUPPORT_LARGE_FUNCTIONS=1` and redeploy. The ffmpeg archive is unpacked to `/tmp` at runtime so the extracted binaries are not part of the bundle.
- fal generations do not block the request. Advance submits, stores the request id, and the next advance or the cron polls once.
- Blob objects are public unguessable URLs so `<img>` and `<video>` can load them after `/file` redirects. The password gates the app and the `/file` route, not a URL someone already has.
