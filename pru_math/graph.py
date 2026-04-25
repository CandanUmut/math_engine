"""Relational knowledge graph.

A NetworkX MultiDiGraph with typed nodes and typed edges, persisted to a
``.gpickle`` file. The graph is kept in memory; every write triggers an
atomic save so the process can crash without losing learning.

Node id scheme
--------------

================  ================================================
``p:{id}``        Problem (id = ``problems.id`` from the SQLite store)
``t:{name}``      Tool (e.g. ``t:sympy``)
``pt:{name}``     Problem type (e.g. ``pt:solve``)
``sig:{hash}``    Fingerprint signature cluster
``r:{name}``      Rule / identity (Phase 2 keeps this minimal; Phase 4+
                  populates it with concrete rewrite rules)
================  ================================================

Edge types
----------

================  ====================================================
``solved_by``     ``p`` → ``t``      attributes: approach, success, verified, time_ms
``has_type``      ``p`` → ``pt``
``has_signature`` ``p`` → ``sig``
``similar_to``    ``p`` ↔ ``p``      undirected pair, attribute ``weight`` ∈ [0, 1]
``uses_rule``     ``p`` → ``r``      reserved for later phases
================  ====================================================

For ``similar_to`` we add directed edges in **both** directions when a
new problem is connected, so Cytoscape / NetworkX traversal sees a
symmetric relation in a directed graph (we use ``MultiDiGraph`` to allow
multiple edge types between the same pair).

Sparse adjacency (``as_sparse``) is computed on demand for similarity
batch queries; it is *not* eagerly maintained because under a few
thousand problems a Python dict scan is faster.
"""
from __future__ import annotations

import os
import pickle
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import networkx as nx

from .config import CONFIG
from .fingerprint import similarity


EDGE_SOLVED_BY = "solved_by"
EDGE_HAS_TYPE = "has_type"
EDGE_HAS_SIG = "has_signature"
EDGE_SIMILAR = "similar_to"
EDGE_USES_RULE = "uses_rule"

NODE_PROBLEM = "problem"
NODE_TOOL = "tool"
NODE_PROBLEM_TYPE = "problem_type"
NODE_SIGNATURE = "signature"
NODE_RULE = "rule"


def problem_node(problem_id: int) -> str:
    return f"p:{int(problem_id)}"


def tool_node(name: str) -> str:
    return f"t:{name}"


def type_node(name: str) -> str:
    return f"pt:{name}"


def signature_node(sig: str) -> str:
    return f"sig:{sig}"


def rule_node(name: str) -> str:
    return f"r:{name}"


@dataclass
class Neighbour:
    problem_id: int
    score: float


class RelationalGraph:
    """In-memory NetworkX graph + atomic gpickle persistence."""

    def __init__(self, path: str | Path | None = None,
                 *, similarity_threshold: float | None = None,
                 autosave: bool = True):
        self.path = Path(path) if path else CONFIG.graph_path
        self.threshold = (
            similarity_threshold if similarity_threshold is not None
            else CONFIG.similarity_threshold
        )
        self.autosave = autosave
        self._lock = threading.RLock()
        self._g: nx.MultiDiGraph = self._load_or_init()

    # --- persistence ----------------------------------------------------

    def _load_or_init(self) -> nx.MultiDiGraph:
        if self.path.is_file() and self.path.stat().st_size > 0:
            try:
                with self.path.open("rb") as fh:
                    g = pickle.load(fh)
                if isinstance(g, nx.MultiDiGraph):
                    return g
            except Exception:
                # Corrupt graph file — start fresh, but back it up.
                backup = self.path.with_suffix(self.path.suffix + ".corrupt")
                try:
                    self.path.rename(backup)
                except Exception:
                    pass
        return nx.MultiDiGraph()

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with tmp.open("wb") as fh:
                pickle.dump(self._g, fh, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, self.path)

    def _maybe_save(self) -> None:
        if self.autosave:
            self.save()

    # --- introspection --------------------------------------------------

    @property
    def graph(self) -> nx.MultiDiGraph:
        return self._g

    def node_count(self) -> int:
        return self._g.number_of_nodes()

    def edge_count(self) -> int:
        return self._g.number_of_edges()

    def stats(self) -> dict[str, int]:
        nodes_by_kind: dict[str, int] = {}
        for _, data in self._g.nodes(data=True):
            kind = data.get("kind", "unknown")
            nodes_by_kind[kind] = nodes_by_kind.get(kind, 0) + 1
        edges_by_kind: dict[str, int] = {}
        for _, _, data in self._g.edges(data=True):
            kind = data.get("kind", "unknown")
            edges_by_kind[kind] = edges_by_kind.get(kind, 0) + 1
        return {
            "nodes": self._g.number_of_nodes(),
            "edges": self._g.number_of_edges(),
            "nodes_by_kind": nodes_by_kind,
            "edges_by_kind": edges_by_kind,
        }

    # --- writes ---------------------------------------------------------

    def add_problem(
        self,
        *,
        problem_id: int,
        problem_type: str,
        signature: str,
        fingerprint: dict[str, Any],
        raw_input: str,
        parsed_pretty: str,
    ) -> str:
        """Add (or update) a problem node and its type / signature edges."""
        with self._lock:
            pn = problem_node(problem_id)
            self._g.add_node(
                pn,
                kind=NODE_PROBLEM,
                problem_id=int(problem_id),
                problem_type=problem_type,
                signature=signature,
                fingerprint=fingerprint,
                raw_input=raw_input,
                parsed_pretty=parsed_pretty,
            )

            tn = type_node(problem_type)
            if not self._g.has_node(tn):
                self._g.add_node(tn, kind=NODE_PROBLEM_TYPE, name=problem_type)
            self._add_edge_unique(pn, tn, kind=EDGE_HAS_TYPE)

            sn = signature_node(signature)
            if not self._g.has_node(sn):
                self._g.add_node(sn, kind=NODE_SIGNATURE, signature=signature, problem_type=problem_type)
            self._add_edge_unique(pn, sn, kind=EDGE_HAS_SIG)
            return pn

    def link_solved_by(
        self,
        *,
        problem_id: int,
        tool: str,
        approach: str,
        success: bool,
        verified: bool,
        time_ms: float,
    ) -> None:
        with self._lock:
            pn = problem_node(problem_id)
            tn = tool_node(tool)
            if not self._g.has_node(tn):
                self._g.add_node(tn, kind=NODE_TOOL, name=tool)
            self._g.add_edge(
                pn, tn,
                key=f"{EDGE_SOLVED_BY}:{approach}",
                kind=EDGE_SOLVED_BY,
                approach=approach,
                success=bool(success),
                verified=bool(verified),
                time_ms=float(time_ms),
            )

    def link_uses_rule(self, problem_id: int, rule_name: str) -> None:
        """Reserved for later phases. Adds a rule node and an edge from the
        problem to it. Idempotent."""
        with self._lock:
            pn = problem_node(problem_id)
            rn = rule_node(rule_name)
            if not self._g.has_node(rn):
                self._g.add_node(rn, kind=NODE_RULE, name=rule_name)
            self._add_edge_unique(pn, rn, kind=EDGE_USES_RULE)

    def add_similarity_edges(
        self,
        *,
        new_problem_id: int,
        candidates: Sequence[tuple[int, float]],
    ) -> int:
        """Connect a newly-added problem to a set of candidates with their
        precomputed scores. Edges below threshold are skipped. Returns the
        number of edges added (per direction; same edge in both directions
        counts as 1)."""
        added = 0
        with self._lock:
            src = problem_node(new_problem_id)
            for pid, score in candidates:
                if pid == new_problem_id:
                    continue
                if score < self.threshold:
                    continue
                dst = problem_node(pid)
                if not self._g.has_node(dst):
                    continue
                self._add_edge_unique(src, dst, kind=EDGE_SIMILAR, weight=float(score))
                self._add_edge_unique(dst, src, kind=EDGE_SIMILAR, weight=float(score))
                added += 1
        return added

    def commit(self) -> None:
        """Force a persistence flush. Use after a batch of writes."""
        self._maybe_save()

    # --- reads ----------------------------------------------------------

    def iter_problem_nodes(self) -> Iterable[tuple[str, dict[str, Any]]]:
        for node, data in self._g.nodes(data=True):
            if data.get("kind") == NODE_PROBLEM:
                yield node, data

    def get_problem_data(self, problem_id: int) -> dict[str, Any] | None:
        node = problem_node(problem_id)
        if node not in self._g:
            return None
        return dict(self._g.nodes[node])

    def neighbours_of_problem(self, problem_id: int, *, top_k: int | None = None
                              ) -> list[Neighbour]:
        """Return ``similar_to`` neighbours sorted by descending weight."""
        out: list[Neighbour] = []
        node = problem_node(problem_id)
        if node not in self._g:
            return out
        for _, dst, data in self._g.out_edges(node, data=True):
            if data.get("kind") != EDGE_SIMILAR:
                continue
            ddata = self._g.nodes.get(dst, {})
            if ddata.get("kind") != NODE_PROBLEM:
                continue
            out.append(Neighbour(problem_id=int(ddata["problem_id"]),
                                 score=float(data.get("weight", 0.0))))
        out.sort(key=lambda n: n.score, reverse=True)
        if top_k is not None:
            out = out[:top_k]
        return out

    def find_similar_to_fingerprint(
        self,
        fingerprint: dict[str, Any],
        *,
        top_k: int = 10,
        same_signature_first: bool = True,
    ) -> list[Neighbour]:
        """Score every existing problem against the given fingerprint and
        return the top-K. Cheap enough for graphs of a few thousand
        problems; for larger graphs see :meth:`as_sparse_similarity`.
        """
        scores: list[Neighbour] = []
        target_sig = fingerprint.get("signature")
        for _, data in self.iter_problem_nodes():
            other_fp = data.get("fingerprint") or {}
            score = similarity(fingerprint, other_fp)
            if same_signature_first and target_sig and other_fp.get("signature") == target_sig:
                # boost exact signature matches so they always sort first
                score = max(score, 0.999)
            scores.append(Neighbour(problem_id=int(data["problem_id"]), score=score))
        scores.sort(key=lambda n: n.score, reverse=True)
        return scores[:top_k]

    # --- subgraphs / serialisation for the UI ---------------------------

    def subgraph_around_problem(self, problem_id: int, *, radius: int = 1) -> dict[str, Any]:
        """Return a JSON-serialisable subgraph around a problem node, suitable
        for cytoscape.js. Includes neighbours up to ``radius`` hops."""
        seed = problem_node(problem_id)
        if seed not in self._g:
            return {"nodes": [], "edges": []}
        nodes = {seed}
        frontier = {seed}
        for _ in range(max(0, radius)):
            nxt: set[str] = set()
            for n in frontier:
                nxt.update(self._g.successors(n))
                nxt.update(self._g.predecessors(n))
            nodes.update(nxt)
            frontier = nxt
        return self._serialise_subgraph(nodes)

    def to_cytoscape(self, *, max_problems: int = 200) -> dict[str, Any]:
        """Full graph as cytoscape JSON. ``max_problems`` caps the number of
        problem nodes returned so the UI stays responsive."""
        problems = [
            (n, d) for n, d in self.iter_problem_nodes()
        ]
        problems.sort(key=lambda nd: -int(nd[1].get("problem_id", 0)))
        kept = {n for n, _ in problems[:max_problems]}
        # Pull in tool/type/signature nodes connected to kept problems.
        connected: set[str] = set(kept)
        for p in kept:
            connected.update(self._g.successors(p))
            connected.update(self._g.predecessors(p))
        return self._serialise_subgraph(connected)

    def _serialise_subgraph(self, node_ids: Iterable[str]) -> dict[str, Any]:
        node_ids = set(node_ids)
        cy_nodes: list[dict[str, Any]] = []
        for nid in node_ids:
            data = self._g.nodes[nid]
            kind = data.get("kind", "unknown")
            label = self._label_for(nid, data)
            cy_nodes.append({
                "data": {
                    "id": nid,
                    "kind": kind,
                    "label": label,
                    "problem_id": data.get("problem_id"),
                    "problem_type": data.get("problem_type"),
                    "signature": data.get("signature"),
                    "raw_input": data.get("raw_input"),
                    "parsed_pretty": data.get("parsed_pretty"),
                    "name": data.get("name"),
                }
            })
        cy_edges: list[dict[str, Any]] = []
        seen_pairs: set[tuple[str, str, str]] = set()
        for u, v, data in self._g.edges(data=True):
            if u not in node_ids or v not in node_ids:
                continue
            kind = data.get("kind", "unknown")
            # Deduplicate undirected similarity edges: keep one direction.
            if kind == EDGE_SIMILAR:
                pair = tuple(sorted([u, v])) + (kind,)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
            cy_edges.append({
                "data": {
                    "id": f"{u}->{v}:{kind}:{data.get('approach', '')}",
                    "source": u,
                    "target": v,
                    "kind": kind,
                    "weight": data.get("weight"),
                    "approach": data.get("approach"),
                    "verified": data.get("verified"),
                    "success": data.get("success"),
                    "time_ms": data.get("time_ms"),
                },
            })
        return {"nodes": cy_nodes, "edges": cy_edges}

    @staticmethod
    def _label_for(nid: str, data: dict[str, Any]) -> str:
        kind = data.get("kind")
        if kind == NODE_PROBLEM:
            pretty = data.get("parsed_pretty") or data.get("raw_input") or ""
            pid = data.get("problem_id")
            short = pretty if len(pretty) <= 28 else pretty[:25] + "…"
            return f"#{pid} {short}"
        if kind == NODE_TOOL:
            return data.get("name") or nid
        if kind == NODE_PROBLEM_TYPE:
            return data.get("name") or nid
        if kind == NODE_SIGNATURE:
            return f"sig {data.get('signature', '')[:8]}"
        if kind == NODE_RULE:
            return data.get("name") or nid
        return nid

    # --- internals ------------------------------------------------------

    def _add_edge_unique(self, u: str, v: str, **attrs: Any) -> None:
        """Add an edge keyed by ``kind`` so we don't accumulate duplicates."""
        kind = attrs.get("kind", "unknown")
        key = kind
        if self._g.has_edge(u, v, key=key):
            # update attributes (e.g. weight may improve)
            self._g.edges[u, v, key].update(attrs)
        else:
            self._g.add_edge(u, v, key=key, **attrs)
