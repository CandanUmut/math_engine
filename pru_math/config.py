"""Runtime configuration loaded from environment variables and .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else (REPO_ROOT / p)


@dataclass(frozen=True)
class Config:
    db_path: Path
    graph_path: Path
    ollama_host: str
    ollama_model: str
    ollama_enabled: bool
    tool_timeout_s: float
    similarity_threshold: float
    similar_top_k: int
    max_attempts: int
    learner_exploration: float


def load_config() -> Config:
    return Config(
        db_path=_resolve(os.getenv("PRU_DB_PATH", "./data/pru_math.sqlite")),
        graph_path=_resolve(os.getenv("PRU_GRAPH_PATH", "./data/pru_graph.gpickle")),
        ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5-math"),
        ollama_enabled=os.getenv("OLLAMA_ENABLED", "true").lower() in {"1", "true", "yes"},
        tool_timeout_s=float(os.getenv("PRU_TOOL_TIMEOUT_S", "20")),
        similarity_threshold=float(os.getenv("PRU_SIM_THRESHOLD", "0.55")),
        similar_top_k=int(os.getenv("PRU_SIMILAR_TOP_K", "5")),
        max_attempts=int(os.getenv("PRU_MAX_ATTEMPTS", "3")),
        learner_exploration=float(os.getenv("PRU_LEARNER_EXPLORATION", "1.0")),
    )


CONFIG = load_config()
