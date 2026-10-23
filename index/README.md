# Search index

`scripts/build_index.py` writes the search index here (`vectors.npz` and `meta.json`): the knowledge base's items and
the Manual's passages as vectors (text-embedding-3-large, 1,024 dimensions), made once with the loaded api key

Without these scripts, the app will still work and says so on screen: however, it will rank knowledge by keywords instead of cosine similarity.

