"""tests/conftest.py — shared test set-up: the repo root on sys.path, and the
knowledge base and a fake search index loaded once for all tests."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.kb import KnowledgeBase  # noqa: E402
from core.retrieval import Retriever, build_index  # noqa: E402
from tests.fake_openai import fake_embed_batch  # noqa: E402


@pytest.fixture(scope="session")
def kb():
    return KnowledgeBase()


@pytest.fixture(scope="session")
def fake_index(kb):
    return build_index(kb, fake_embed_batch)


@pytest.fixture(scope="session")
def retriever(kb, fake_index):
    return Retriever(kb, fake_index)
