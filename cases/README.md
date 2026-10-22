# cases/ — where the case database lives

The cases chunked on the **Case database** page are saved here automatically (`core/case_store.py`), after
every change, and loaded again when the app starts:

| File | What it holds | Commit it? |
|---|---|---|
| `case_database.sqlite3` | SQLite: every case (its text word for word, labels, figures) and its embedding (text-embedding-3-large, 1024 dimensions) | **Yes** — this is the record of the database |
| `chroma/` | ChromaDB (persistent client): the same embeddings in a collection searched by cosine similarity when the estimate picks the most similar cases | No — rebuilt from the SQLite file whenever it is missing or out of step (`.gitignore` leaves it out) |
| `database.json` | A JSON copy downloaded from the page (optional). Loaded only the first time, when no SQLite file exists yet, to seed it | Optional |

The files hold no API key. They belong to the server the app runs on and are shared by everyone using it. On
Streamlit Community Cloud files written while the app runs last until it is rebooted or redeployed, so to keep the
database for good — and to give your marker the same cases — commit `cases/case_database.sqlite3` and push.

If ChromaDB cannot be imported on the server, the page says so and the cases are ranked with NumPy (the same cosine
similarity, computed exactly); nothing is lost.

Case summaries published by the Judicial College of Victoria are its material: check its terms of use before
committing them to a public repository (a private repository shared with your marker avoids publishing them).
