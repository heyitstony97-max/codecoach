"""Prerequisite-graph helpers: in-order fallback and minimal cycle breaking. Pure stdlib, deterministic."""

from itertools import combinations

Edge = tuple[str, str]   # (prereq, objective)

_EXACT_LIMIT = 18        # SCCs with at most this many edges are solved exactly


def fallback_edges(ordered_ids: list[str]) -> list[Edge]:
    """Deterministic in-order chain: each objective needs the one before it."""
    return [(a, b) for a, b in zip(ordered_ids, ordered_ids[1:])]


def clean_edges(edges: list[Edge], known: set[str]) -> list[Edge]:
    """Drop unknown ids, self-loops and duplicates; keep first-seen order."""
    seen: set[Edge] = set()
    out: list[Edge] = []
    for a, b in edges:
        if a in known and b in known and a != b and (a, b) not in seen:
            seen.add((a, b))
            out.append((a, b))
    return out


def is_acyclic(edges: list[Edge]) -> bool:
    nodes = {x for e in edges for x in e}
    indeg = {n: 0 for n in nodes}
    out: dict[str, list[str]] = {n: [] for n in nodes}
    for a, b in edges:
        out[a].append(b)
        indeg[b] += 1
    ready = [n for n, d in indeg.items() if d == 0]
    seen = 0
    while ready:
        n = ready.pop()
        seen += 1
        for m in out[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
    return seen == len(nodes)


def _sccs(edges: list[Edge]) -> list[set[str]]:
    """Strongly connected components with more than one node (iterative Tarjan)."""
    out: dict[str, list[str]] = {}
    for a, b in edges:
        out.setdefault(a, []).append(b)
        out.setdefault(b, [])
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on: set[str] = set()
    stack: list[str] = []
    result: list[set[str]] = []
    counter = 0
    for root in sorted(out):
        if root in index:
            continue
        work = [(root, iter(out[root]))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on.add(nxt)
                    work.append((nxt, iter(out[nxt])))
                    advanced = True
                    break
                if nxt in on:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp: set[str] = set()
                while True:
                    x = stack.pop()
                    on.discard(x)
                    comp.add(x)
                    if x == node:
                        break
                if len(comp) > 1:
                    result.append(comp)
    return result


def _find_cycle(edges: list[Edge]) -> list[Edge]:
    out: dict[str, list[str]] = {}
    for a, b in edges:
        out.setdefault(a, []).append(b)
    state: dict[str, int] = {}
    for root in sorted(out):
        if state.get(root):
            continue
        path = [root]
        state[root] = 1
        its = [iter(out.get(root, []))]
        while its:
            for nxt in its[-1]:
                if state.get(nxt) == 1:
                    cyc = path[path.index(nxt):] + [nxt]
                    return list(zip(cyc, cyc[1:]))
                if not state.get(nxt):
                    state[nxt] = 1
                    path.append(nxt)
                    its.append(iter(out.get(nxt, [])))
                    break
            else:
                state[path.pop()] = 2
                its.pop()
    return []


def _drop_set(scc_edges: list[Edge], pos: dict[str, int]) -> list[Edge]:
    # backward edges (prereq later in the document than the objective) are the likeliest mistakes
    ranked = sorted(scc_edges, key=lambda e: (-(pos[e[0]] - pos[e[1]]), pos[e[0]], pos[e[1]]))
    if len(ranked) <= _EXACT_LIMIT:
        for k in range(1, len(ranked) + 1):
            for combo in combinations(range(len(ranked)), k):
                gone = set(combo)
                if is_acyclic([e for i, e in enumerate(ranked) if i not in gone]):
                    return [ranked[i] for i in combo]
    dropped: list[Edge] = []
    left = list(ranked)
    while True:
        cyc = _find_cycle(left)
        if not cyc:
            return dropped
        worst = max(cyc, key=lambda e: (pos[e[0]] - pos[e[1]], -pos[e[0]]))
        dropped.append(worst)
        left.remove(worst)


def break_cycles(edges: list[Edge], order: list[str]) -> tuple[list[Edge], list[Edge], int]:
    """Return (kept, dropped, cycles_repaired). `order` is document order of objective ids.

    Each strongly connected component (a tangle of cycles) loses the fewest edges that make it
    acyclic: exact search for small components, a greedy heuristic above that. cycles_repaired is
    the number of components that needed repair. Kept edges keep their original order."""
    pos = {o: i for i, o in enumerate(order)}
    edges = [e for e in edges if e[0] in pos and e[1] in pos and e[0] != e[1]]
    dropped: list[Edge] = []
    repaired = 0
    for comp in _sccs(edges):
        repaired += 1
        dropped += _drop_set([e for e in edges if e[0] in comp and e[1] in comp], pos)
    gone = set(dropped)
    return [e for e in edges if e not in gone], dropped, repaired
