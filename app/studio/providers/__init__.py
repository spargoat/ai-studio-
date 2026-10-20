from .base import ProviderError, NonRetryable
from .mock import MockProvider
from .ffmpeg_local import FfmpegProvider
from .fal import FalProvider
from .elevenlabs import ElevenLabsProvider
from .llm import LLMProvider
from .higgsfield import HiggsfieldProvider

KINDS = {"mock": MockProvider, "ffmpeg": FfmpegProvider, "fal": FalProvider,
         "elevenlabs": ElevenLabsProvider, "llm": LLMProvider, "higgsfield": HiggsfieldProvider}


def make(name, cfg):
    pcfg = cfg["providers"][name]
    return KINDS[pcfg["kind"]](name, pcfg)
