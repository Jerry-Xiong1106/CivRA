"""Run a two- or four-setting development pilot; credentials stay in memory."""

import argparse
import getpass
import json
import os
from pathlib import Path
import ssl
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.common.config import load_dotenv

from backend.agent.factory import llm_from_env  # noqa: E402
from backend.agent.model.llm import make_chat_model  # noqa: E402
from backend.knowledge.config import RetrievalConfig  # noqa: E402
from backend.knowledge.retrieval.service import RetrievalService  # noqa: E402
from evaluation.adapters.team import ModelAdapter, RetrievalAdapter  # noqa: E402
from evaluation.runners.pilot import run_pilot  # noqa: E402
from evaluation.runners.consultation import ConsultationPilot  # noqa: E402
from evaluation.schemas.tasks import HiddenTask, load_public_tasks, read_records  # noqa: E402


def pilot_client(config, prompt_key):
    provider = config["provider"]
    key_name = {"siliconflow": "SILICONFLOW_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}.get(provider)
    if not key_name:
        raise ValueError(f"Unsupported pilot provider: {provider}")
    key = getpass.getpass(f"{key_name} (not saved): ") if prompt_key else os.environ.get(key_name)
    if not key:
        raise ValueError(f"Set {key_name} or use --prompt-key")
    if provider == "deepseek":
        client = make_chat_model(model=config["model"], base_url="https://api.deepseek.com",
                                 api_key=key, timeout_s=60)
        return client.model_copy(update={"extra_body": {"thinking": {"type": "disabled"}}})
    os.environ[key_name] = key
    os.environ.setdefault("CIVRA_LLM_API_KEY", key)
    os.environ.setdefault("CIVRA_LLM_BASE_URL", "https://api.siliconflow.cn/v1")
    return llm_from_env(config["model"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "evaluation/configs/pilot_v1.json")
    parser.add_argument("--public", type=Path, default=ROOT / "evaluation/datasets/pilot/tasks.jsonl")
    parser.add_argument("--output-root", type=Path, default=ROOT / "evaluation/results")
    parser.add_argument("--prompt-key", action="store_true")
    parser.add_argument("--four-mode", action="store_true", help="Run the four-setting consultation pilot")
    parser.add_argument("--hidden", type=Path, help="Controller-only hidden-fact JSONL for --four-mode")
    args = parser.parse_args()
    load_dotenv()
    config = json.loads(args.config.read_text())
    if not config.get("paid_execution_enabled") or not config.get("model"):
        parser.error("Model and paid_execution_enabled must be set in the pilot plan")
    # Use a verified CA bundle when provided by the isolated environment.
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        ssl.create_default_context()
    try:
        client = pilot_client(config, args.prompt_key)
    except ValueError as exc:
        parser.error(str(exc))
    service = RetrievalService.from_config(RetrievalConfig.from_mode(
        config["retrieval"]["mode"], variant=config["retrieval"]["variant"],
        top_k=config["retrieval"]["top_k"]))
    model, retrieval = ModelAdapter(client), RetrievalAdapter(service)
    if not args.four_mode:
        run_pilot(args.public, config, model, retrieval, args.output_root, ROOT)
        return
    if args.hidden is None:
        parser.error("--four-mode requires --hidden")
    tasks = load_public_tasks(args.public)
    hidden = {row.task_id: row for row in read_records(args.hidden, HiddenTask)}
    if set(hidden) != {task.task_id for task in tasks} or any(
        task.dataset_version != hidden[task.task_id].dataset_version
        or (task.task_type == "static" and (hidden[task.task_id].facts or hidden[task.task_id].route_choice))
        for task in tasks
    ):
        parser.error("The hidden-fact records do not match the public development tasks")
    with TemporaryDirectory(prefix="civra-evaluation-") as directory:
        consultation = ConsultationPilot(model, retrieval, hidden, Path(directory))
        run_pilot(args.public, config, model, retrieval, args.output_root, ROOT, consultation=consultation)


if __name__ == "__main__":
    main()
