from __future__ import annotations

from collections import defaultdict, deque

from .model import Model
from .reporting import AnalysisResult, EvaluationState


def evaluate_analyses(model: Model) -> list[AnalysisResult]:
    results: list[AnalysisResult] = []
    source_facts = [f for f in model.fact_types.values() if f.id not in model.field_hints]

    for name, args in model.analyses:
        if name == "uniform":
            if len(args) != 1 or not args[0].isdigit():
                results.append(AnalysisResult("uniform", EvaluationState.UNEVALUATED, "uniform requires one integer arity"))
                continue
            n = int(args[0])
            offenders = [f.name for f in source_facts if len(f.roles) != n]
            if offenders:
                results.append(AnalysisResult(f"uniform({n})", EvaluationState.VIOLATED, f"facts with non-{n} arity: {', '.join(sorted(offenders))}"))
            else:
                results.append(AnalysisResult(f"uniform({n})", EvaluationState.PASSED, f"all source fact types have arity {n}"))
        elif name == "connected":
            nodes: set[str] = set()
            adj: dict[str, set[str]] = defaultdict(set)
            for fact in source_facts:
                fnode = fact.id
                nodes.add(fnode)
                for role in fact.roles:
                    onode = role.player_id
                    nodes.add(onode)
                    adj[fnode].add(onode)
                    adj[onode].add(fnode)
            if not nodes:
                results.append(AnalysisResult("connected", EvaluationState.NOT_APPLICABLE, "model has no source fact graph"))
            else:
                start = next(iter(nodes))
                seen = {start}
                q = deque([start])
                while q:
                    cur = q.popleft()
                    for nxt in adj[cur]:
                        if nxt not in seen:
                            seen.add(nxt)
                            q.append(nxt)
                if seen == nodes:
                    results.append(AnalysisResult("connected", EvaluationState.PASSED, "all participating object and fact types lie in one incidence component"))
                else:
                    results.append(AnalysisResult("connected", EvaluationState.VIOLATED, f"incidence graph has disconnected nodes ({len(nodes)-len(seen)} unreachable)"))
        elif name == "directed":
            results.append(AnalysisResult("directed", EvaluationState.UNEVALUATED, "fact-oriented roles encode orientation; no separate directedness predicate is defined"))
        else:
            results.append(AnalysisResult(name, EvaluationState.UNEVALUATED, "analysis rule is not implemented"))
    return results
