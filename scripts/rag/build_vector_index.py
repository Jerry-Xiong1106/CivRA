"""Build SiliconFlow dense indexes; completed embedding batches are cached."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.knowledge.vector.embedding import EmbeddingClient
from backend.knowledge.vector.vector_index import build_vector_index, save_vector_index
from backend.knowledge.config import CORPUS_DIR, INDEX_DIR, INPUT_VARIANTS, vector_index_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=CORPUS_DIR)
    parser.add_argument("--variant", choices=(*INPUT_VARIANTS, "both"), default="both")
    parser.add_argument("--output-dir", type=Path, default=INDEX_DIR)
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    client = EmbeddingClient()
    for variant in INPUT_VARIANTS if args.variant == "both" else (args.variant,):
        index = build_vector_index(sorted(args.corpus_dir.glob("*.jsonl")), client, variant, args.batch_size)
        path = vector_index_path(variant, args.output_dir)
        save_vector_index(index, path)
        print(f"Saved {path}: {len(index.provisions)} provisions, {index.config['dimensions']} dimensions", flush=True)


if __name__ == "__main__":
    main()
