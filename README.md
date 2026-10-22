# AI Studio

Source lives in [`app/`](app/). See [app/README.md](app/README.md) for the stage runner.

Jobs started from the API run on Inngest when `STUDIO_EXECUTOR=inngest` (see [app/README.md](app/README.md#durable-jobs-on-vercel-inngest)). Production needs `INNGEST_EVENT_KEY` and `INNGEST_SIGNING_KEY`, set by the Vercel Inngest integration. Local `studio serve` and the dry-run tests stay on background threads unless that flag is set.
