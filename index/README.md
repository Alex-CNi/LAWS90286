# Search index

`scripts/build_index.py` writes the search index here (`vectors.npz` and `meta.json`): the knowledge base's items and
the Manual's passages as vectors (text-embedding-3-large, 1,024 dimensions), made once with the developer's own key
from `.env`. The files hold no key. Commit them before submitting (see the README checklist).

Without them the app still works and says so on screen: it ranks knowledge by keywords instead of cosine similarity.
