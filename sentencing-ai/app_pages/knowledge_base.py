"""
app_pages/knowledge_base.py — SEARCH AND BROWSE THE KNOWLEDGE BASE.

  * Search: cosine similarity over the stored vectors when an index exists and
    a key is entered (the query is embedded with the user's key); otherwise
    keyword ranking. Results show each item's statement and verbatim quotes.
  * The offence register rows for one chapter of Part E.
  * Build the search index with your own key (if it has not been built).
This page makes no AI calls (only an embeddings request for a search).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app_pages.common import get_kb, get_keyword_retriever, get_retriever, has_key, make_llm, show_citations
from app_pages.theme import hero
from core import config
from core.llm import LLMError
from core.retrieval import build_index

kb = get_kb()
retriever = get_retriever()

hero("What does the Manual say?",
     f"Everything the app knows about the law: {len(kb.meta)} items and {len(kb.chunks)} passages extracted from the "
     f"{config.MANUAL_NAME} (version of {config.MANUAL_VERSION_DATE}), and the offence register ({len(kb.offences)} "
     f"Victorian offences). Search: {retriever.description}.")

# ── Search: the white bar (words, then the offence chapter), and the filters under it ──
FILES = sorted({m["file"] for m in kb.meta.values()})
STAGES = list(kb.stage_map)
st.session_state.setdefault("kb_chapter", "25")     # the estimate page's offence panel can open another chapter
with st.container(key="search_bar"):
    c1, c2, c3 = st.columns([6.2, 3.2, 0.8], gap="small", vertical_alignment="center")
    query = c1.text_input("Search the knowledge base", key="kb_query", label_visibility="collapsed",
                          placeholder="Search the Manual — e.g. glassing, special reasons, parity")
    chapter = c2.selectbox("Offence chapter (Part E)", config.PART_E_CHAPTERS, key="kb_chapter",
                           format_func=kb.chapter_title, label_visibility="collapsed")
    c3.button("Search", key="kb_go", help="Search")
c1, c2, c3 = st.columns([2, 2, 3], vertical_alignment="bottom")
files = c1.multiselect("Kinds", FILES, default=[f for f in FILES if f not in ("intake", "audit")])
stages = c2.multiselect("Stages", STAGES)
chapter_offences = [o for o in config.IN_SCOPE_OFFENCES if kb.offence_chapter(o) == chapter]
linked_only = c3.checkbox("Only items linked to this chapter's offences, or general items", value=True)

if query:
    ids = [i for i, m in kb.meta.items() if m["file"] in files and (not stages or set(m.get("stage") or []) & set(stages))
           and m.get("jurisdiction") != "cth"]
    if linked_only:
        ids = [i for i in ids if not kb.meta[i].get("offence_ids")
               or set(kb.meta[i]["offence_ids"]) & set(chapter_offences)]
    q = query
    if retriever.uses_vectors and has_key():
        cache = st.session_state.setdefault("query_vectors", {})     # embed each search once per session
        if query not in cache:
            try:
                cache[query] = make_llm().embed("Knowledge base search", [query])[0][0]
            except LLMError as exc:
                st.warning(f"{exc} — using keyword search instead.")
        q = cache.get(query, query)
    # A vector query needs the vector index; a text query needs keyword ranking.
    ranker = retriever if not isinstance(q, str) or not retriever.uses_vectors else get_keyword_retriever()
    hits = ranker.rank("item", q, ids, 15)
    passages = ranker.rank("chunk", q, [c for c, ch in kb.chunks.items() if ch.get("jurisdiction") != "Cth"], 5)
    st.markdown(f"**{len(hits)} items**")
    for item_id, score in hits:
        m = kb.meta[item_id]
        with st.expander(f"{kb.title(item_id)} · {m['file']} · {', '.join(m.get('stage') or [])} · score {score:.2f}"):
            st.markdown(f"`{item_id}` — {kb.statement(item_id)}")
            for c in kb.citations_for(item_id, 4):
                st.markdown(f"> {c.quote}\n>\n> — *{c.label}*")
                for fn in c.footnotes[:3]:
                    st.caption(f"fn {fn.n}: {fn.text[:220]}")
    st.markdown("**Manual passages**")
    for cid, score in passages:
        show_citations(kb, [], [cid], label=f"{kb.chunks[cid]['section']} {kb.chunks[cid].get('title', '')} · score {score:.2f}")

# ── The offence register (the chosen chapter) ───────────────────────────────
st.subheader(f"Offence register — chapter {kb.chapter_title(chapter)}")
rows = []
for oid in chapter_offences:
    o = kb.offence(oid)
    m = (o.get("maxima") or [{}])[0]
    rows.append({"Offence": kb.offence_label(oid), "Maximum": m.get("text"), "From": m.get("from"),
                 "Category": "; ".join(f"Cat {c['category']}: {c.get('condition')}" for c in o.get("category") or []),
                 "Minimum non-parole period": "; ".join(x.get("text", "") for x in o.get("minimum_non_parole") or []),
                 "Register confidence": o.get("confidence")})
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

# ── Build the index with your own key ───────────────────────────────────────
with st.expander("Search index"):
    meta = retriever.index.meta if retriever.index is not None else None
    if meta:
        st.markdown(f"Built {meta.get('built_at')} with {meta.get('model')} ({meta.get('dimensions')} dimensions): "
                    f"{meta.get('items')} items, {meta.get('chunks')} passages. Engine: {retriever.description}.")
    else:
        st.markdown("No usable index. The developer normally builds it once with `python scripts/build_index.py`. "
                    "You can build it here with your own key: about 450,000 tokens of embeddings (roughly US$0.06) "
                    "and a minute or two. It is saved on the server for later visitors and holds no key.")
        if st.button("Build the search index with my key", disabled=not has_key()):
            llm = make_llm()
            bar = st.progress(0.0, text="Embedding…")
            try:
                index = build_index(kb, lambda texts: llm.embed("Build index", texts)[0],
                                    lambda f, msg: bar.progress(f, text=msg))
                index.save(config.INDEX_DIR)
                get_retriever.clear()
                st.success("Index built. Reload the page.")
            except LLMError as exc:
                st.error(str(exc))
            except OSError as exc:
                st.error(f"Built, but could not save the index on this server: {exc}")
