
# scripts/build_index.py: one-off dev script that embeds knowledge/ and the Manual into index/ (commit it); needs OPENAI_API_KEY or .env, costs about US$0.06.
# Re-run after changing knowledge/ or the embedding settings, or the app falls back to keyword search.

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openai import OpenAI  # noqa: E402

from core import config  # noqa: E402
from core.kb import KnowledgeBase  # noqa: E402
from core.retrieval import build_index  # noqa: E402


def read_key() -> str:
    """$OPENAI_API_KEY, else the OPENAI_API_KEY line of .env."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("OPENAI_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key or key.startswith("sk-..."):
        sys.exit("No key found: put OPENAI_API_KEY=... in .env (copy .env.example) or set the environment variable.")
    return key


def main():
    client = OpenAI(api_key=read_key())
    kb = KnowledgeBase()

    def embed(texts):
        resp = client.embeddings.create(model=config.EMBEDDING_MODEL, input=texts,
                                        dimensions=config.EMBEDDING_DIMENSIONS)
        return [d.embedding for d in resp.data]

    def progress(fraction, message):
        print(f"  {fraction:6.1%}  {message}", flush=True)

    print(f"Embedding the knowledge base with {config.EMBEDDING_MODEL} ({config.EMBEDDING_DIMENSIONS} dims)…")
    index = build_index(kb, embed, progress)
    index.save(config.INDEX_DIR)
    print(f"Saved {len(index.item_ids)} item vectors and {len(index.chunk_ids)} passage vectors to {config.INDEX_DIR}")
    print("Commit index/vectors.npz and index/meta.json to GitHub.")


if __name__ == "__main__":
    main()
