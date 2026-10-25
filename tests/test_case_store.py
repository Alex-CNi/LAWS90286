"""Tests of core/case_store.py: the case database saved in the app's files (SQLite + the ChromaDB collection),
loaded again when a session starts, and used by the estimate."""

import json
import sqlite3

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from core import cases as cs
from core import config
from core.case_store import CHROMA_DIR, SQLITE_FILE, CaseStore, StoreError, collection_name
from tests.fake_openai import fake_vector
from tests.test_app import APP, KEY, button
from tests.test_cases_db import S17, small_db

pytest.importorskip("chromadb")      # the app falls back to NumPy without it; these tests check the collection too


def new_app():
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    return at


def test_the_database_is_saved_to_sqlite_and_loaded_again_whole(isolated_case_store):
    db = small_db(with_vectors=True)
    db.cases[1].use = False
    db.origin = "uploaded documents"
    store = CaseStore()
    assert not store.exists() and store.load() is None
    store.save(db)
    assert (isolated_case_store / SQLITE_FILE).exists()
    con = sqlite3.connect(isolated_case_store / SQLITE_FILE)
    rows = con.execute("SELECT id, position, vector IS NOT NULL FROM cases ORDER BY position").fetchall()
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    con.close()
    assert [r[0] for r in rows] == [c.id for c in db.cases] and all(r[2] for r in rows)
    assert meta["embedding_model"] == config.EMBEDDING_MODEL and meta["last_number"] == str(db.last_number)
    back = CaseStore().load()
    assert [c.to_dict() for c in back.cases] == [c.to_dict() for c in db.cases]
    assert back.last_number == db.last_number and back.origin == "uploaded documents"
    assert set(back.vectors) == set(db.vectors)
    for k in db.vectors:
        assert np.allclose(back.vectors[k], db.vectors[k])
    assert not back.cases[1].use and back.search is not None


def test_the_chroma_collection_follows_the_file_and_ranks_like_numpy(isolated_case_store):
    db = small_db(with_vectors=True)
    store = CaseStore()
    store.save(db)
    info = store.info()
    assert info["cases"] == 7 and info["vectors"] == 7 and info["chroma"] == 7 and not info["chroma_error"]
    assert (isolated_case_store / CHROMA_DIR).exists()
    q = np.array(fake_vector("glass hotel face scar"), dtype=np.float32)
    q /= np.linalg.norm(q)
    ids = [c.id for c in db.cases]
    sims = store.vectors.similarities(q, ids)
    assert sims is not None and set(sims) == set(ids)
    for k in ids:
        assert abs(sims[k] - float(np.dot(db.vectors[k], q))) < 1e-4
    # the comparables use the collection; without it (or when it cannot answer), NumPy
    comp = cs.select_comparables(db, S17, query_vector=q, k=3)
    assert "ChromaDB" in comp.method and comp.selected
    db.search = lambda query, ids: None
    comp2 = cs.select_comparables(db, S17, query_vector=q, k=3)
    assert "NumPy" in comp2.method and [e["id"] for e in comp2.selected] == [e["id"] for e in comp.selected]
    # a case removed: gone from the collection on the next save; a case without a vector is not in it
    db.remove({"C2"})
    db.vectors.pop("C3")
    store.save(db)
    assert store.vectors.count() == 5
    assert store.vectors.similarities(q, ["C1", "C3"]) is None         # cannot answer for C3


def test_vectors_from_another_embedding_model_are_dropped_on_loading(isolated_case_store, monkeypatch):
    store = CaseStore()
    store.save(small_db(with_vectors=True))
    old = collection_name()
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "text-embedding-other")
    back = CaseStore().load()
    assert len(back.cases) == 7 and not back.vectors and len(back.missing_vectors()) == 7
    assert collection_name() != old and CaseStore().vectors.count() == 0      # the old collection is gone


def test_a_damaged_file_raises_a_store_error_and_a_cleared_store_loads_nothing(isolated_case_store):
    store = CaseStore()
    store.save(small_db(with_vectors=False))
    con = sqlite3.connect(isolated_case_store / SQLITE_FILE)
    con.execute("UPDATE cases SET data = 'not json' WHERE id = 'C1'")
    con.commit()
    con.close()
    with pytest.raises(StoreError, match="C1"):
        CaseStore().load()
    # the app starts empty, says so, and leaves the damaged file alone (even with a committed database.json)
    from app_pages.common import get_committed_case_db  # noqa: PLC0415
    (isolated_case_store / "database.json").write_text(small_db(with_vectors=False).to_json(), encoding="utf-8")
    get_committed_case_db.clear()
    try:
        at = new_app()
        at.switch_page("app_pages/cases.py").run()
        assert not at.exception, at.exception
        assert any("could not be" in e.value or "not valid JSON" in e.value for e in at.error)
        assert any(m.label == "Cases" and str(m.value) == "7" for m in at.metric)       # the repository's copy
        con = sqlite3.connect(isolated_case_store / SQLITE_FILE)
        assert con.execute("SELECT data FROM cases WHERE id = 'C1'").fetchone()[0] == "not json"
        con.close()
    finally:
        get_committed_case_db.clear()
    store.clear()
    assert not store.exists() and CaseStore().load() is None


def test_the_app_saves_every_change_and_a_new_session_starts_from_the_files(isolated_case_store, monkeypatch):
    """The user's complaint: cases chunked on the page were lost with the session. Now a change on the page is
    written to cases/case_database.sqlite3 and the ChromaDB collection, and a new session (another AppTest, the
    same files) starts from them; the estimate ranks them through the collection."""
    from tests.test_app import fake  # noqa: PLC0415 — the fixture function, applied by hand
    fake.__wrapped__(monkeypatch)
    at = new_app()
    at.switch_page("app_pages/cases.py").run()
    assert any("Nothing saved yet" in m.value for m in at.markdown)
    db = small_db(with_vectors=True)                                 # AppTest cannot upload files
    db.cases[6].use = False
    at.session_state["case_db"] = db
    at.run()
    at.checkbox(key="cdb_discard_ok").check().run()
    button(at, "Delete the 1 case(s)").click().run()                 # a change: saved
    assert not at.exception, at.exception
    assert (isolated_case_store / SQLITE_FILE).exists()
    assert any(f"cases/{SQLITE_FILE}" in m.value and "6 case(s), 6 with embeddings" in m.value for m in at.markdown)
    assert any(collection_name() in m.value and "6 embedding(s)" in m.value for m in at.markdown)

    at2 = new_app()                                                   # a new session
    at2.switch_page("app_pages/cases.py").run()
    assert not at2.exception, at2.exception
    assert any(m.label == "Cases" and str(m.value) == "6" for m in at2.metric)
    assert any(m.label == "With embeddings" and str(m.value) == "6" for m in at2.metric)
    db = at2.session_state["case_db"]
    assert [c.id for c in db.cases] == ["C1", "C2", "C3", "C4", "C5", "C6"] and db.search is not None
    # an edit in the table is saved too
    at2.session_state["case_db"].cases[0].use = False
    at2.session_state["case_db"].cases[0].edited = True
    from app_pages.common import save_case_db  # noqa: PLC0415
    save_case_db(at2.session_state["case_db"])
    assert not CaseStore().load().cases[0].use
    # and the estimate uses the collection
    at2.switch_page("app_pages/estimate.py").run()
    at2.sidebar.text_input[0].input(KEY).run()
    at2.sidebar.button[0].click().run()
    at2.selectbox(key="test_case_choice").set_value("tc1_glassing_rcsi.json").run()
    button(at2, "Extract the facts").click().run()
    button(at2, "Confirm the facts").click().run()
    assert any("Case database: 6 cases" in c.value for c in at2.caption)
    button(at2, "Run the analysis").click().run()
    assert not at2.exception, at2.exception
    comp = at2.session_state.run.comparables
    assert "ChromaDB" in comp.method and comp.selected


def test_a_committed_json_database_seeds_the_files_once(isolated_case_store):
    from app_pages.common import get_committed_case_db  # noqa: PLC0415
    get_committed_case_db.clear()
    (isolated_case_store / "database.json").write_text(small_db(with_vectors=True).to_json(), encoding="utf-8")
    try:
        at = new_app()
        at.switch_page("app_pages/cases.py").run()
        assert not at.exception, at.exception
        assert any(m.label == "Cases" and str(m.value) == "7" for m in at.metric)
        assert (isolated_case_store / SQLITE_FILE).exists()
        assert "repository" in at.session_state["case_db"].origin
        saved = CaseStore().load()
        assert len(saved.cases) == 7 and len(saved.vectors) == 7
        # the files now rule: clearing the database is remembered by the next session
        at.checkbox(key="cdb_discard_ok").check().run()
        button(at, "Clear the database").click().run()
        at2 = new_app()
        at2.switch_page("app_pages/cases.py").run()
        assert any(m.label == "Cases" and str(m.value) == "0" for m in at2.metric)
        assert json.loads(open(isolated_case_store / "database.json", encoding="utf-8").read())["cases"]
        button(at2, "Go back to the repository's database (7 cases)").click().run()
        assert any(m.label == "Cases" and str(m.value) == "7" for m in at2.metric)
    finally:
        get_committed_case_db.clear()


def test_the_page_says_which_cases_an_estimate_would_use(isolated_case_store):
    from tests.test_cases_db import S17, S16, BURGLARY
    at = new_app()
    at.switch_page("app_pages/cases.py").run()
    at.session_state["case_db"] = small_db(with_vectors=True)
    at.run()
    at.selectbox(key="cdb_check_charge").set_value(S17).run()
    assert not at.exception, at.exception
    assert any("would be compared" in s.value for s in at.success)
    at.selectbox(key="cdb_check_charge").set_value(BURGLARY).run()           # no burglary cases: none used
    assert any("No case in use would reach" in e.value for e in at.error)
    at.selectbox(key="cdb_check_charge").set_value(S16).run()               # same section as s 17: the related tier
    assert any("would be compared" in s.value for s in at.success)
