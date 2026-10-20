class ProviderError(Exception):
    """A provider call failed. Retryable unless NonRetryable."""


class NonRetryable(ProviderError):
    pass


class Provider:
    paid = False

    def __init__(self, name, pcfg):
        self.name = name
        self.cfg = pcfg

    def run(self, kind, ctx):
        """Run one call. ctx keys: model, prompt, params, inputs{name: path}, out_dir,
        persona, script, video_seconds, audio_seconds, attempt.
        Returns dict(output_path, meta, units_actual, request_id)."""
        fn = getattr(self, f"do_{kind}", None)
        if fn is None:
            raise NonRetryable(f"provider {self.name} cannot run stage kind '{kind}'")
        return fn(ctx)
