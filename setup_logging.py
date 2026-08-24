import logging
import os


def configure_logging() -> None:
    """
    Configure root logging once, at app startup. Reads SHELF_MG_LOG_LEVEL
    from the environment (falls back to INFO) so log verbosity can be
    adjusted per-deployment (e.g. via docker-compose.yml) without a
    code change.
    """
    level_name = os.environ.get("SHELF_MG_LOG_LEVEL", "DEBUG").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s.%(funcName)s: %(message)s",
    )
