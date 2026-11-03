"""Build SiliconFlow dense indexes; completed embedding batches are cached."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.knowledge.vector.embedding import EmbeddingClient
from backend.knowledge.vector.vector_index import build_vector_index, save_vector_index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=Path("data/processed"))
    parser.add_argument("--variant", choices=("text", "metadata", "both"), default="both")
    parser.add_argument("--output-dir", type=Path, default=Path("data/index"))
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    client = EmbeddingClient()
    for variant in ("text", "metadata") if args.variant == "both" else (args.variant,):
        index = build_vector_index(sorted(args.corpus_dir.glob("*.jsonl")), client, variant == "metadata", args.batch_size)
        path = args.output_dir / f"vector_{variant}.npz"
        save_vector_index(index, path)
        print(f"Saved {path}: {len(index.provisions)} provisions, {index.config['dimensions']} dimensions", flush=True)


if __name__ == "__main__":
    main()
