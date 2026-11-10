"""Execute the declared two-mode development pilot; credentials stay in memory."""

import argparse
import getpass
import json
import os
from pathlib import Path
import ssl
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agent.llm import OpenAICompatibleClient  # noqa: E402
from backend.knowledge.config import RetrievalConfig  # noqa: E402
from backend.knowledge.retrieval.service import RetrievalService  # noqa: E402
from evaluation.adapters.team import ModelAdapter, RetrievalAdapter  # noqa: E402
from evaluation.runners.pilot import run_pilot  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "evaluation/configs/pilot_v1.json")
    parser.add_argument("--public", type=Path, default=ROOT / "evaluation/datasets/pilot/tasks.jsonl")
    parser.add_argument("--output-root", type=Path, default=ROOT / "evaluation/results")
    parser.add_argument("--prompt-key", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if not config.get("paid_execution_enabled") or not config.get("model"):
        parser.error("Model and paid_execution_enabled must be set in the pilot plan")
    if args.prompt_key:
        os.environ["SILICONFLOW_API_KEY"] = getpass.getpass("SiliconFlow API key (not saved): ")
    key = os.environ.get("SILICONFLOW_API_KEY", "")
    if not key:
        parser.error("Set SILICONFLOW_API_KEY or use --prompt-key")
    os.environ.setdefault("CIVRA_LLM_API_KEY", key)
    os.environ.setdefault("CIVRA_LLM_BASE_URL", "https://api.siliconflow.cn/v1")
    os.environ.setdefault("CIVRA_LLM_MODEL", config["model"])
    # Use a verified CA bundle when provided by the isolated environment.
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        ssl.create_default_context()
    client = OpenAICompatibleClient(timeout_s=120)
    service = RetrievalService.from_config(RetrievalConfig.from_mode(
        config["retrieval"]["mode"], variant=config["retrieval"]["variant"],
        top_k=config["retrieval"]["top_k"]))
    run_pilot(args.public, config, ModelAdapter(client), RetrievalAdapter(service), args.output_root, ROOT)


if __name__ == "__main__":
    main()
