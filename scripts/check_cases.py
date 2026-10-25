"""
scripts/check_cases.py — WHY DOES AN ESTIMATE SHOW (OR NOT SHOW) COMPARABLE CASES?

Run from the repository root:   python scripts/check_cases.py [offence id]

Prints what is in cases/case_database.sqlite3 (and the ChromaDB collection), how the cases are labelled, and — for
the charge given (default: s 17 recklessly causing serious injury) — how many cases each tier of the comparables
policy would use, what a dry run of select_comparables returns, and how many of those cases the result card could
place in a range. No API key, no AI calls.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config  # noqa: E402
from core.agreement import case_outcome  # noqa: E402
from core.case_store import CaseStore  # noqa: E402
from core.cases import related_offences, select_comparables  # noqa: E402
from core.kb import KnowledgeBase  # noqa: E402

DEFAULT = "crimes-act-s-17-recklessly-causing-serious-injury"


def main() -> None:
    oid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    kb = KnowledgeBase()
    store = CaseStore()
    print(f"Store: {store.path} — exists: {store.exists()}"
          + (f", {store.path.stat().st_size / 1e6:.1f} MB" if store.exists() else ""))
    db = store.load()
    if db is None:
        print("Nothing saved in the store. The app would start from cases/database.json if there is one.")
        return
    info = store.info()
    print(f"Cases: {len(db.cases)} | in use: {len(db.usable())} | with embeddings: {len(db.vectors)} | "
          f"ChromaDB collection: {info['chroma']}" + (f" (error: {info['chroma_error']})" if info["chroma_error"] else ""))
    print(f"Embedding model recorded: {config.EMBEDDING_MODEL} ({config.EMBEDDING_DIMENSIONS} dims); origin: {db.origin}")

    labels = Counter(c.principal_offence_id if c.principal_offence_id in kb.offences else "OTHER / no register label"
                     for c in db.cases)
    print("\nPrincipal offence labels (top 25):")
    for k, n in labels.most_common(25):
        print(f"  {n:5d}  {kb.charge_label(k) if k in kb.offences else k}")
    other = [c for c in db.cases if c.principal_offence_id not in kb.offences]
    if other:
        names = Counter(c.principal_offence.strip().lower() for c in other)
        print(f"\n{len(other)} case(s) have no register label. Their offence names as written (top 15):")
        for k, n in names.most_common(15):
            print(f"  {n:5d}  {k}")

    if oid not in kb.offences:
        print(f"\nUnknown offence id: {oid}")
        return
    usable = db.usable()
    same = [c for c in usable if c.principal_offence_id == oid]
    also = [c for c in usable if c.principal_offence_id != oid and oid in {o.get("offence_id") for o in c.offences}]
    rel = related_offences(kb, oid)
    related = [c for c in usable if c.principal_offence_id in rel and c not in also]
    print(f"\nCharge: {kb.charge_label(oid)}")
    print(f"  same offence: {len(same)} | charge also among the offences: {len(also)} | same section "
          f"({len(rel)} offences): {len(related)} | would reach an estimate: {len(same) + len(also) + len(related)}")

    comp = select_comparables(db, oid, query_vector=None, query_text="assault injury glass hotel punch", k=config.CASES_FOR_COMPARISON,
                              case_name="", narrative="", related=rel)
    print(f"\nDry run of select_comparables (keyword ranking, no key): method = {comp.method}")
    print(f"  selected: {len(comp.selected)} | left out: {len(comp.left_out)} | note: {comp.note or '—'}")
    for e in comp.selected[:8]:
        print(f"   {e['id']:5s} {e['match']:40s} {e['case']['citation'][:60]}")
    if comp.selected:
        rows = [case_outcome(e, oid) for e in comp.selected]
        with_outcome = [r for r in rows if r.kind]
        print(f"\nResult card: {len(with_outcome)} of {len(rows)} selected cases have a sentence for this offence that can be "
              f"placed against a range (the share needs {config.AGREEMENT_MIN_CASES}).")
        for r in rows:
            print(f"   {r.id:5s} {(r.outcome or '—')[:38]:38s} {r.reason[:70]}")

if __name__ == "__main__":
    main()
