"""Knowledge base with hybrid search (dense vectors in ChromaDB + BM25 keywords, fused with RRF).

Every chunk is one policy clause and carries its document metadata (doc_id, version, effective
date, authority), so each answer can cite document, section, version and effective date (R2).
Only clauses that the resolver says are in force on the as-of date are searched (R4).
"""

import json
import logging
import math
import threading
from collections import Counter

import chromadb
import pandas as pd
from chromadb.config import Settings
from rank_bm25 import BM25Okapi

from app import config
from app.kb import resolver, text
from app.kb.embedder import get_embedder

log = logging.getLogger(__name__)


def _doc_from_row(row):
    level = row.get("authority_level", "") or "hospital_policy"
    rank = row.get("authority_rank") or resolver.AUTHORITY_RANK.get(level, 3)
    return {
        "doc_key": f"{row['doc_id']}@{row['version']}",
        "doc_id": row["doc_id"], "title": row.get("title", ""), "policy_key": row.get("policy_key", ""),
        "doc_type": row.get("doc_type", ""), "authority_level": level, "authority_rank": int(rank),
        "version": str(row["version"]), "effective_date": str(row.get("effective_date", "")),
        "expiry_date": str(row.get("expiry_date", "") or ""), "status": row.get("status", "approved"),
        "supersedes": row.get("supersedes", "") or "", "department": row.get("department", ""),
        "origin": row.get("origin", "csv"), "partial": False,
    }


class KnowledgeBase:
    def __init__(self):
        self._lock = threading.Lock()
        self.embedder = get_embedder()
        self.docs, self.chunks = [], []
        self._load_csv()
        self._load_ingested()
        self._index_all()

    # ------------------------------------------------------------ loading

    def _load_csv(self):
        pol = pd.read_csv(config.RAW_DIR / "hospital_policies.csv", dtype=str, keep_default_na=False)
        sec = pd.read_csv(config.RAW_DIR / "hospital_policy_sections.csv", dtype=str, keep_default_na=False)
        docs = {r["doc_id"]: _doc_from_row(r) for r in pol.to_dict("records")}
        for r in sec.to_dict("records"):
            d = docs[r["doc_id"]]
            self._add_chunk(d, r["clause_number"], r["section_number"], r["section_title"], r["text"])
        self.docs = list(docs.values())
        self._mark_partial()

    def _load_ingested(self):
        registry = config.INGESTED_DIR / "registry.json"
        if not registry.exists():
            return
        for entry in json.loads(registry.read_text(encoding="utf-8")):
            doc = _doc_from_row(entry["meta"])
            doc["origin"] = "ingested"
            self.docs.append(doc)
            for cl in entry["clauses"]:
                self._add_chunk(doc, cl["clause"], cl["section_number"], cl["section_title"], cl["text"])
        self._mark_partial()

    def _add_chunk(self, doc, clause, section_number, section_title, body):
        """One chunk per clause. Tables are split into one chunk per row (header repeated), so a
        question about one row ("HbA1c turnaround") is not drowned by a long table. All row chunks
        keep the clause's citation id."""
        base = {"section_id": f"{doc['doc_id']}#{clause}", "clause": clause, "section_number": section_number,
                "section_title": section_title,
                **{k: doc[k] for k in ("doc_key", "doc_id", "title", "policy_key", "doc_type", "authority_level",
                                       "authority_rank", "version", "effective_date", "expiry_date", "status",
                                       "supersedes")}}
        lines = [ln for ln in body.splitlines() if ln.strip()]
        table = [ln for ln in lines if ln.strip().startswith("|")]
        if len(table) >= 4:
            intro = "\n".join(ln for ln in lines if not ln.strip().startswith("|"))
            for i, row in enumerate(table[2:], start=1):
                piece = "\n".join(x for x in (intro, table[0], table[1], row) if x)
                self.chunks.append({**base, "chunk_id": f"{doc['doc_key']}#{clause}~{i}", "text": piece})
        else:
            self.chunks.append({**base, "chunk_id": f"{doc['doc_key']}#{clause}", "text": body})

    def _mark_partial(self):
        for d in self.docs:
            d["partial"] = any("remain unchanged" in c["text"].lower() for c in self.chunks if c["doc_key"] == d["doc_key"])

    # ------------------------------------------------------------ indexing

    @staticmethod
    def _index_text(c):
        return f"{c['title']} - {c['section_title']}\n{c['text']}"

    def _index_all(self):
        self._build_lexical()
        self._sync_chroma()

    def _build_lexical(self):
        self.by_id = {c["chunk_id"]: c for c in self.chunks}
        self._positions = {c["chunk_id"]: i for i, c in enumerate(self.chunks)}
        corpus = [text.tokens(self._index_text(c)) for c in self.chunks]
        self.bm25 = BM25Okapi(corpus)
        self.token_sets = {c["chunk_id"]: set(toks) for c, toks in zip(self.chunks, corpus)}
        df = Counter(t for toks in corpus for t in set(toks))
        self.idf = {t: math.log(1 + len(corpus) / f) for t, f in df.items()}
        self.max_idf = math.log(1 + len(corpus))

    def _sync_chroma(self):
        config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(config.CHROMA_DIR),
                                           settings=Settings(anonymized_telemetry=False))
        name = "policies_" + "".join(ch if ch.isalnum() else "_" for ch in self.embedder.name)[:50]
        self.collection = client.get_or_create_collection(name, embedding_function=None,
                                                          metadata={"hnsw:space": "cosine"})
        existing = set(self.collection.get(include=[])["ids"])
        wanted = set(self.by_id)
        stale = list(existing - wanted)
        if stale:
            self.collection.delete(ids=stale)
        missing = [self.by_id[i] for i in self.by_id if i not in existing]
        if missing:
            self._add_to_chroma(missing)

    def _add_to_chroma(self, chunks):
        for start in range(0, len(chunks), 128):
            batch = chunks[start:start + 128]
            self.collection.add(
                ids=[c["chunk_id"] for c in batch],
                embeddings=self.embedder.encode([self._index_text(c) for c in batch]).tolist(),
                documents=[c["text"] for c in batch],
                metadatas=[{"chunk_id": c["chunk_id"], "doc_id": c["doc_id"], "version": c["version"],
                            "clause": c["clause"], "effective_date": c["effective_date"],
                            "authority_rank": c["authority_rank"], "status": c["status"]} for c in batch])

    # ------------------------------------------------------------ search

    def usable_ids(self, as_of=None):
        return resolver.usable_chunk_ids(self.docs, self.chunks, as_of or config.AS_OF_DATE)

    def coverage(self, query, chunk_id):
        """IDF-weighted share of the query's content words found in the clause (synonyms count)."""
        terms = text.content_terms(query)
        if not terms:
            return 0.0
        have = self.token_sets[chunk_id]
        total = got = 0.0
        for t in terms:
            weight = self.idf.get(t, self.max_idf)
            total += weight
            alternatives = {t} | {text.stem(s) for s in text.SYNONYMS.get(t, [])}
            if alternatives & have:
                got += weight
        return got / total if total else 0.0

    def search(self, query, as_of=None, k=None, doc_ids=None):
        """Hybrid search over clauses in force on as_of. Returns hits sorted by score (best first)."""
        k = k or config.TOP_K
        with self._lock:
            allowed = self.usable_ids(as_of)
            if doc_ids:
                allowed = {i for i in allowed if self.by_id[i]["doc_id"] in set(doc_ids)}
            if not allowed:
                return []
            q = text.expand_query(query)
            pool = min(len(allowed), 40)

            dense = self.collection.query(query_embeddings=self.embedder.encode([q]).tolist(), n_results=pool,
                                          where={"chunk_id": {"$in": sorted(allowed)}},
                                          include=["distances"])
            dense_ids = dense["ids"][0]
            dense_sim = {cid: 1 - d for cid, d in zip(dense_ids, dense["distances"][0])}

            scores = self.bm25.get_scores(text.content_terms(q) or text.tokens(q))
            bm25_ranked = sorted((cid for cid in allowed), key=lambda cid: -scores[self._pos(cid)])[:pool]
            bm25_score = {cid: float(scores[self._pos(cid)]) for cid in bm25_ranked}

            fused = Counter()
            for rank, cid in enumerate(dense_ids):
                fused[cid] += 1 / (config.RRF_K + rank + 1)
            for rank, cid in enumerate(bm25_ranked):
                if bm25_score[cid] > 0:
                    fused[cid] += 1 / (config.RRF_K + rank + 1)

        top = max(fused.values()) if fused else 1
        when_q, qty_q = bool(text.WHEN_Q.search(query)), bool(text.QUANTITY_Q.search(query))
        hits = []
        for cid, rrf in fused.items():
            c = self.by_id[cid]
            cov = self.coverage(query, cid)
            boost = (0.2 if when_q and text.TIME_RE.search(c["text"]) else 0) + \
                    (0.15 if qty_q and text.NUMBER_RE.search(c["text"]) else 0)
            hits.append({**c, "rrf": rrf, "dense": round(dense_sim.get(cid, 0.0), 4),
                         "bm25": round(bm25_score.get(cid, 0.0), 3), "coverage": round(cov, 3),
                         "score": round(rrf / top + 0.35 * cov + boost, 4)})
        hits.sort(key=lambda h: -h["score"])
        return hits[:k]

    def _pos(self, chunk_id):
        return self._positions[chunk_id]

    # ------------------------------------------------------------ live ingestion

    def add_document(self, meta, clauses):
        """Add a validated document (R11). Searchable immediately; no restart needed."""
        with self._lock:
            doc = _doc_from_row({**meta, "origin": "ingested"})
            doc["origin"] = "ingested"
            if any(d["doc_key"] == doc["doc_key"] for d in self.docs):
                raise ValueError(f"{doc['doc_key']} is already in the knowledge base.")
            self.docs.append(doc)
            start = len(self.chunks)
            for cl in clauses:
                self._add_chunk(doc, cl["clause"], cl["section_number"], cl["section_title"], cl["text"])
            self._mark_partial()
            new_chunks = self.chunks[start:]
            self._build_lexical()
            self._add_to_chroma(new_chunks)
            return doc, new_chunks

    # ------------------------------------------------------------ helpers

    def doc(self, doc_key):
        return next((d for d in self.docs if d["doc_key"] == doc_key), None)

    def usable_sections(self, section_ids, as_of=None):
        """Look up clauses by citation id (doc_id#clause), keeping only the version in force."""
        allowed = self.usable_ids(as_of)
        wanted = set(section_ids)
        return [c for c in self.chunks if c["section_id"] in wanted and c["chunk_id"] in allowed]

    def usable_sections_by_doc(self, doc_key, as_of=None):
        allowed = self.usable_ids(as_of)
        return [c for c in self.chunks if c["doc_key"] == doc_key and c["chunk_id"] in allowed]

    def stats(self):
        return {"documents": len(self.docs), "clauses": len(self.chunks), "embedder": self.embedder.name,
                "vector_store": "ChromaDB", "keyword_index": "BM25", "fusion": "Reciprocal Rank Fusion"}


_kb = None


def get_kb():
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb


def reset_kb():
    global _kb
    _kb = None
