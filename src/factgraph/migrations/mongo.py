from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from ..diff import ChangeSafety, MigrationHints, align_models, semantic_diff
from ..ids import slug
from ..model import EntityType, Model
from ..targets import mongo as mg
from .base import TargetMigrationOperation, TargetMigrationPlan


_PHASE_ORDER = {"rename": 10, "transition": 15, "create": 20, "data": 30, "indexes": 40, "validator": 50, "cleanup": 90, "manual": 95}


def _oid(*parts: object) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":")).encode()
    return "mgmig:" + hashlib.sha256(raw).hexdigest()[:16]


def _operation(phase: str, kind: str, safety: ChangeSafety, automatic: bool, description: str, *, target=None, command=None, preflight=None, rollback=None, notes=(), parameters=None) -> TargetMigrationOperation:
    return TargetMigrationOperation(
        _oid(phase, kind, target, description, command, preflight), phase, kind, safety, automatic,
        description, target, command, preflight, rollback, tuple(notes), dict(parameters or {})
    )


def _coll_map(before: Model, after: Model, hints: MigrationHints) -> dict[str, str]:
    align = align_models(before, after, hints)
    bp = {c["source_name"]: c for c in mg.build_plan(before)["collections"]}
    ap = {c["source_name"]: c for c in mg.build_plan(after)["collections"]}
    out: dict[str, str] = {}
    for source, coll in bp.items():
        if coll["conceptual_kind"] in {"entity", "subtype_entity"}:
            mapped = align.object_types.get(source)
        else:
            mapped = align.facts.get(source)
        if mapped is not None and mapped in ap:
            out[coll["name"]] = ap[mapped]["name"]
    return out


def _prop_map_for_collection(before: Model, after: Model, old: dict, new: dict, hints: MigrationHints) -> dict[str, str]:
    align = align_models(before, after, hints)
    old_props = set(old["validator"]["$jsonSchema"].get("properties", {}))
    new_props = set(new["validator"]["$jsonSchema"].get("properties", {}))
    out = {p: p for p in sorted(old_props & new_props)}

    if old["conceptual_kind"] in {"entity", "subtype_entity"} and new["conceptual_kind"] in {"entity", "subtype_entity"}:
        old_entity = before.object_type_by_name(old["source_name"])
        new_entity = after.object_type_by_name(new["source_name"])
        if isinstance(old_entity, EntityType) and isinstance(new_entity, EntityType):
            for (oo, of), (no, nf) in align.fields.items():
                if oo == old_entity.name and no == new_entity.name:
                    op, np = slug(of), slug(nf)
                    if op in old_props and np in new_props:
                        out[op] = np
            old_ids, new_ids = mg._ids(before, old_entity.id), mg._ids(after, new_entity.id)
            if len(old_ids) == len(new_ids):
                for oh, nh in zip(old_ids, new_ids):
                    ok = (before.object_types[oh.owner_object_type_id].name, oh.field_name)
                    nk = align.fields.get(ok)
                    if nk is not None and nk[1] == nh.field_name:
                        op, np = slug(oh.field_name), slug(nh.field_name)
                        if op in old_props and np in new_props:
                            out[op] = np

    if old["conceptual_kind"] in {"fact", "objectified_fact"} and new["conceptual_kind"] in {"fact", "objectified_fact"}:
        old_fact, new_fact = before.fact_by_name(old["source_name"]), after.fact_by_name(new["source_name"])
        rmap = align.roles.get(old_fact.name, {})
        broles, aroles = {r.name: r for r in old_fact.roles}, {r.name: r for r in new_fact.roles}
        for orname, nrname in rmap.items():
            oprops = list(mg._role_properties(before, broles[orname]))
            nprops = list(mg._role_properties(after, aroles[nrname]))
            if len(oprops) == len(nprops):
                for op, np in zip(oprops, nprops):
                    if op in old_props and np in new_props:
                        out[op] = np
        bobj, aobj = before.objectification_for_fact(old_fact.id), after.objectification_for_fact(new_fact.id)
        if bobj is not None and aobj is not None:
            for (oo, of), (no, nf) in align.fields.items():
                if oo == bobj.name and no == aobj.name:
                    op, np = slug(of), slug(nf)
                    if op in old_props and np in new_props:
                        out[op] = np
        if "_id" in old_props and "_id" in new_props:
            out["_id"] = "_id"
    return out


def _rename_refs(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {mapping.get(k, k): _rename_refs(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [_rename_refs(v, mapping) for v in value]
    if isinstance(value, str) and value.startswith("$"):
        raw = value[1:]
        return "$" + mapping.get(raw, raw)
    return value


def _mapped_validator(old_validator: dict, mapping: dict[str, str]) -> dict:
    v = deepcopy(old_validator)
    js = v.get("$jsonSchema", {})
    props = js.get("properties", {})
    js["properties"] = {mapping.get(k, k): _rename_refs(val, mapping) for k, val in props.items()}
    js["required"] = sorted(mapping.get(x, x) for x in js.get("required", []))
    if "$expr" in v:
        v["$expr"] = _rename_refs(v["$expr"], mapping)
    return v


def _transition_validator(old_validator: dict, new_validator: dict, mapping: dict[str, str]) -> dict:
    v = deepcopy(old_validator)
    js = v.setdefault("$jsonSchema", {})
    props = js.setdefault("properties", {})
    new_props = new_validator.get("$jsonSchema", {}).get("properties", {})
    required = set(js.get("required", []))
    for old, new in mapping.items():
        if old == new:
            continue
        if new in new_props:
            props[new] = deepcopy(new_props[new])
        required.discard(old)
    js["required"] = sorted(required)
    # Cross-field expressions referring to fields being renamed are removed in
    # the transition validator and restored by the final validator.
    v.pop("$expr", None)
    return v


def _default_index_name(keys: dict[str, int]) -> str:
    return "_".join(f"{k}_{v}" for k, v in keys.items())


def _map_index(index: dict, mapping: dict[str, str]) -> tuple:
    keys = tuple((mapping.get(k, k), v) for k, v in index["keys"].items())
    return keys, bool(index.get("unique"))


def _index_command(coll: str, index: dict) -> str:
    keys = json.dumps(index["keys"], sort_keys=False, separators=(",", ":"))
    opts = json.dumps({"unique": bool(index.get("unique"))}, sort_keys=True, separators=(",", ":"))
    return f"db.getCollection({json.dumps(coll)}).createIndex({keys}, {opts});"


def _unique_preflight(coll: str, index: dict) -> str:
    fields = list(index["keys"])
    group_id = {f: f"${f}" for f in fields}
    return (
        f"db.getCollection({json.dumps(coll)}).aggregate(["
        f"{{$group:{{_id:{json.dumps(group_id, separators=(',', ':'))},n:{{$sum:1}}}}}},"
        f"{{$match:{{n:{{$gt:1}}}}}},{{$limit:20}}]);"
    )


def _validator_safety(old: dict, new: dict) -> ChangeSafety:
    ojs, njs = old.get("$jsonSchema", {}), new.get("$jsonSchema", {})
    oreq, nreq = set(ojs.get("required", [])), set(njs.get("required", []))
    if nreq - oreq:
        return ChangeSafety.REQUIRES_DATA_CHECK
    op, np = ojs.get("properties", {}), njs.get("properties", {})
    for name in set(op) & set(np):
        a, b = op[name], np[name]
        if a.get("bsonType") != b.get("bsonType"):
            return ChangeSafety.MANUAL
        if "minimum" in b and ("minimum" not in a or b["minimum"] > a["minimum"]):
            return ChangeSafety.REQUIRES_DATA_CHECK
        if "maximum" in b and ("maximum" not in a or b["maximum"] < a["maximum"]):
            return ChangeSafety.REQUIRES_DATA_CHECK
        if "enum" in b:
            old_enum = set(a.get("enum", b["enum"]))
            if not old_enum.issubset(set(b["enum"])):
                return ChangeSafety.REQUIRES_DATA_CHECK
    if "$expr" in new and "$expr" not in old:
        return ChangeSafety.REQUIRES_DATA_CHECK
    if "$expr" in old and "$expr" in new and old["$expr"] != new["$expr"]:
        return ChangeSafety.REQUIRES_DATA_CHECK
    return ChangeSafety.SAFE


def _validator_preflight(coll: str, old: dict, new: dict) -> str:
    # MongoDB can ask the server to evaluate the target validator against the
    # current collection by using $jsonSchema/$expr in a find filter. This is a
    # preview query, not a proof for unsupported cross-collection semantics.
    clauses = []
    if "$jsonSchema" in new:
        clauses.append({"$nor": [{"$jsonSchema": new["$jsonSchema"]}]})
    if "$expr" in new:
        clauses.append({"$expr": {"$not": [new["$expr"]]}})
    query = clauses[0] if len(clauses) == 1 else {"$or": clauses} if clauses else {}
    return f"db.getCollection({json.dumps(coll)}).find({json.dumps(query, sort_keys=True, separators=(',', ':'))}).limit(20);"


def _create_collection_commands(coll: dict) -> str:
    validator = json.dumps(coll["validator"], sort_keys=True, separators=(",", ":"))
    lines = [f"db.createCollection({json.dumps(coll['name'])}, {{validator:{validator}}});"]
    for idx in coll["indexes"]:
        lines.append(_index_command(coll["name"], idx))
    return "\n".join(lines)


def build_plan(before: Model, after: Model, hints: MigrationHints | None = None) -> TargetMigrationPlan:
    hints = hints or MigrationHints.empty()
    before_plan, after_plan = mg.build_plan(before), mg.build_plan(after)
    bc = {c["name"]: c for c in before_plan["collections"]}
    ac = {c["name"]: c for c in after_plan["collections"]}
    cmap = _coll_map(before, after, hints)
    mapped_old, mapped_new = set(cmap), set(cmap.values())
    ops: list[TargetMigrationOperation] = []
    warnings: list[str] = []

    for name in sorted(set(ac) - mapped_new):
        coll = ac[name]
        ops.append(_operation("create", "create_collection", ChangeSafety.SAFE, True,
            f"create new collection {name}", target=name, command=_create_collection_commands(coll),
            rollback=f"db.getCollection({json.dumps(name)}).drop();", parameters={"collection": name, "spec": coll}))

    for name in sorted(set(bc) - mapped_old):
        ops.append(_operation("cleanup", "drop_collection", ChangeSafety.DESTRUCTIVE, False,
            f"drop collection {name} and all documents", target=name,
            command=f"db.getCollection({json.dumps(name)}).drop();",
            preflight=f"db.getCollection({json.dumps(name)}).countDocuments({{}});", parameters={"collection": name}))

    for old_name, new_name in sorted(cmap.items()):
        old, new = bc[old_name], ac[new_name]
        current = new_name
        if old_name != new_name:
            ops.append(_operation("rename", "rename_collection", ChangeSafety.SAFE, True,
                f"rename collection {old_name} to {new_name}", target=old_name,
                command=f"db.getCollection({json.dumps(old_name)}).renameCollection({json.dumps(new_name)});",
                rollback=f"db.getCollection({json.dumps(new_name)}).renameCollection({json.dumps(old_name)});", parameters={"collection": old_name, "new_name": new_name}))

        pmap = _prop_map_for_collection(before, after, old, new, hints)
        old_props = old["validator"]["$jsonSchema"].get("properties", {})
        new_props = new["validator"]["$jsonSchema"].get("properties", {})
        renamed = {o: n for o, n in pmap.items() if o != n}

        # Indexed field renames are intentionally manual: MongoDB index key paths
        # do not rename with document fields, and unique indexes on missing fields
        # can make naive staged $rename updates fail halfway through.
        indexed_old = {k for idx in old["indexes"] for k in idx["keys"]}
        automatic_renames = {o: n for o, n in renamed.items() if o not in indexed_old}
        manual_renames = {o: n for o, n in renamed.items() if o in indexed_old}

        if automatic_renames:
            transition = _transition_validator(old["validator"], new["validator"], automatic_renames)
            tjson = json.dumps(transition, sort_keys=True, separators=(",", ":"))
            ops.append(_operation("transition", "relax_validator_for_rename", ChangeSafety.REQUIRES_DATA_CHECK, False,
                f"temporarily relax {current} validator for non-indexed field renames", target=current,
                command=f"db.runCommand({{collMod:{json.dumps(current)},validator:{tjson}}});",
                preflight="// run the destination-field collision preflights before entering the transition window",
                notes=("this operation is gated together with the data rename; safe-only scripts never weaken the live validator",), parameters={"collection": current, "validator": transition}))
            for oldp, newp in sorted(automatic_renames.items()):
                filt = json.dumps({oldp: {"$exists": True}}, separators=(",", ":"))
                upd = json.dumps({"$rename": {oldp: newp}}, separators=(",", ":"))
                pref = json.dumps({newp: {"$exists": True}}, separators=(",", ":"))
                ops.append(_operation("data", "rename_field_data", ChangeSafety.REQUIRES_DATA_CHECK, False,
                    f"rename document field {current}.{oldp} to {newp}", target=current,
                    command=f"db.getCollection({json.dumps(current)}).updateMany({filt},{upd});",
                    preflight=f"db.getCollection({json.dumps(current)}).countDocuments({pref});",
                    notes=("preflight must be zero to avoid overwriting an already-populated destination field",), parameters={"collection": current, "old_field": oldp, "new_field": newp}))
        for oldp, newp in sorted(manual_renames.items()):
            filt = json.dumps({oldp: {"$exists": True}}, separators=(",", ":"))
            upd = json.dumps({"$rename": {oldp: newp}}, separators=(",", ":"))
            pref = json.dumps({newp: {"$exists": True}}, separators=(",", ":"))
            ops.append(_operation("manual", "rename_indexed_field", ChangeSafety.MANUAL, False,
                f"rename indexed field {current}.{oldp} to {newp}", target=current,
                command=f"db.getCollection({json.dumps(current)}).updateMany({filt},{upd});",
                preflight=f"db.getCollection({json.dumps(current)}).countDocuments({pref});",
                notes=("coordinate validator relaxation plus drop/recreate of affected indexes; the planner refuses a partial automatic sequence",), parameters={"collection": current, "old_field": oldp, "new_field": newp}))

        mapped_old_validator = _mapped_validator(old["validator"], pmap)
        validator_changed = mapped_old_validator != new["validator"]
        if validator_changed or automatic_renames or manual_renames:
            safety = _validator_safety(mapped_old_validator, new["validator"]) if validator_changed else ChangeSafety.SAFE
            if automatic_renames and safety == ChangeSafety.SAFE:
                safety = ChangeSafety.REQUIRES_DATA_CHECK
            if manual_renames:
                safety = ChangeSafety.MANUAL
            auto = safety == ChangeSafety.SAFE
            validator = json.dumps(new["validator"], sort_keys=True, separators=(",", ":"))
            preflight = _validator_preflight(current, mapped_old_validator, new["validator"]) if validator_changed and safety != ChangeSafety.SAFE else None
            ops.append(_operation("validator", "update_validator", safety, auto,
                f"update validator for {current} to the after-model schema", target=current,
                command=f"db.runCommand({{collMod:{json.dumps(current)},validator:{validator}}});",
                preflight=preflight,
                notes=(("restore/finalize the target validator only after all gated data renames/backfills succeed",) if automatic_renames or manual_renames else ("existing documents are not automatically rewritten; future writes must satisfy the final validator",)), parameters={"collection": current, "validator": new["validator"]}))

        # Added/removed document properties that are not renames. A removed
        # property remains physically present unless destructive cleanup is enabled.
        mapped_old_props = set(pmap.values())
        added_props = set(new_props) - mapped_old_props
        dropped_props = set(old_props) - set(pmap)
        for prop in sorted(dropped_props):
            if prop == "_id":
                continue
            ops.append(_operation("cleanup", "unset_removed_field", ChangeSafety.DESTRUCTIVE, False,
                f"remove obsolete field {current}.{prop} from existing documents", target=current,
                command=f"db.getCollection({json.dumps(current)}).updateMany({{{json.dumps(prop)}:{{$exists:true}}}},{{$unset:{{{json.dumps(prop)}:\"\"}}}});",
                preflight=f"db.getCollection({json.dumps(current)}).countDocuments({{{json.dumps(prop)}:{{$exists:true}}}});",
                notes=("validator changes alone do not delete stored fields because the canonical schema permits additional properties",), parameters={"collection": current, "field": prop}))
        for prop in sorted(added_props):
            if prop in new["validator"]["$jsonSchema"].get("required", []):
                missing = json.dumps({prop: {"$exists": False}}, separators=(",", ":"))
                ops.append(_operation("manual", "backfill_required_field", ChangeSafety.MANUAL, False,
                    f"backfill new required field {current}.{prop}", target=current,
                    preflight=f"db.getCollection({json.dumps(current)}).countDocuments({missing});",
                    notes=("no source-independent default is available; provide an application-specific data migration before enabling the final validator",), parameters={"collection": current, "field": prop}))

        # Index changes after mapping property names.
        old_idx = {_map_index(i, pmap): i for i in old["indexes"]}
        new_idx = {_map_index(i, {}): i for i in new["indexes"]}
        for key in sorted(set(new_idx) - set(old_idx), key=str):
            idx = new_idx[key]
            safety = ChangeSafety.REQUIRES_DATA_CHECK if idx.get("unique") else ChangeSafety.SAFE
            ops.append(_operation("indexes", "create_index", safety, safety == ChangeSafety.SAFE,
                f"create {'unique ' if idx.get('unique') else ''}index on {current}{tuple(idx['keys'])}", target=current,
                command=_index_command(current, idx),
                preflight=_unique_preflight(current, idx) if idx.get("unique") else None, parameters={"collection": current, "index": idx}))
        for key in sorted(set(old_idx) - set(new_idx), key=str):
            idx = old_idx[key]
            index_name = _default_index_name(idx["keys"])
            ops.append(_operation("indexes", "drop_index", ChangeSafety.SAFE, True,
                f"drop obsolete index {index_name} on {current}", target=current,
                command=f"db.getCollection({json.dumps(current)}).dropIndex({json.dumps(index_name)});", parameters={"collection": current, "index_name": index_name}))

    sem = semantic_diff(before, after, hints)
    if any(c.kind.value in {"add_subtype", "drop_subtype", "change_subtype"} for c in sem.changes):
        warnings.append("subtype population/identity changes are semantic-manual operations; MongoDB collection shape changes do not prove cross-collection inclusion")
    if hints.roles:
        warnings.append("fact-role renames usually touch the fact-set unique index and therefore remain manual for MongoDB")

    ops.sort(key=lambda op: (_PHASE_ORDER.get(op.phase, 50), op.target_object or "", op.kind, op.id))
    return TargetMigrationPlan("mongo", before.name, after.name, ops, warnings)


def _render_script(plan: TargetMigrationPlan, *, include_risky: bool, include_destructive: bool) -> str:
    lines = [
        "// generated by factgraph v0.5 semantic migration planner",
        f"// {plan.before_model} -> {plan.after_model}",
        "// Safe operations are executable; gated/manual/destructive operations are commented unless enabled by this preview.",
        "",
    ]
    for op in plan.operations:
        lines.append(f"// [{op.safety.value}] {op.description}")
        if op.preflight:
            for ln in op.preflight.splitlines():
                lines.append("// preflight: " + ln)
        permitted = op.automatic
        if op.safety == ChangeSafety.REQUIRES_DATA_CHECK and include_risky and op.command:
            permitted = True
        if op.safety == ChangeSafety.DESTRUCTIVE and include_destructive and op.command:
            permitted = True
        if op.safety == ChangeSafety.MANUAL:
            permitted = False
        if op.command:
            if permitted:
                lines.extend(op.command.splitlines())
            else:
                lines.extend("// BLOCKED: " + ln for ln in op.command.splitlines())
        else:
            lines.append("// no automatic command emitted")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def emit_safe_script(plan: TargetMigrationPlan) -> str:
    return _render_script(plan, include_risky=False, include_destructive=False)


def emit_risky_preview_script(plan: TargetMigrationPlan) -> str:
    return _render_script(plan, include_risky=True, include_destructive=False)


def emit_destructive_preview_script(plan: TargetMigrationPlan) -> str:
    return _render_script(plan, include_risky=True, include_destructive=True)


def emit_preflight_script(plan: TargetMigrationPlan) -> str:
    lines = [
        "// generated preflight queries for gated factgraph MongoDB migration operations",
        f"// {plan.before_model} -> {plan.after_model}",
        "",
    ]
    for op in plan.operations:
        if op.preflight:
            lines.append(f"// {op.id}: [{op.safety.value}] {op.description}")
            lines.extend(op.preflight.splitlines())
            lines.append("")
    if len(lines) == 3:
        lines.append("// no preflight queries required")
    return "\n".join(lines).rstrip() + "\n"
