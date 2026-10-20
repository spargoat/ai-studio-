"""LLM text stages (visual bible, creative director, script/hooks, scene beats).
No key is configured yet, so live runs stop with a clean 'needs key' error and nothing is called.
Dry runs never reach this class (the mock provider answers instead)."""
from .base import Provider, NonRetryable
from ..config import key_status


class LLMProvider(Provider):
    paid = True

    def do_llm(self, ctx):
        name, ok = key_status(self.cfg)
        if not ok:
            raise NonRetryable(f"needs key: LLM stage '{ctx['stage']}' needs one of {self.cfg.get('key_env')} "
                               "(none set). Nothing was called.")
        raise NonRetryable(f"LLM adapter for {ctx['model']} is not implemented yet (key {name} is present). "
                           "Nothing was called.")

    do_script = do_llm
