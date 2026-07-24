"""MLB value engine: scrape Betclic odds, enrich with MLB data, propose EV+ combinés."""

__version__ = "1.0.0"

from .pipeline import run_pipeline

__all__ = ["run_pipeline", "__version__"]
