# Factum ORM Import

Factgraph can use a native Factum `.orm.json` file as an **audit source**. The purpose is interoperability, not to replace Factum as an ORM authoring/verbalization environment.

## CLI

```bash
factgraph import-factum model.orm.json --out-dir artifacts/imported
factgraph audit model.orm.json --out-dir artifacts/audit
```

`audit` auto-detects Factum JSON when the document contains Factum-style `objectTypes` and `factTypes`; `--input-format factum` is also available.

The importer writes/preserves:

- original source in the audit handoff;
- canonical normalized Factgraph representation;
- semantic and manifest JSON;
- import report with every unsupported/lossy/approximated source construct;
- validation report.

## Identity

Identity priority:

1. Factum `meta.guid` when present;
2. Factum local element ID otherwise.

Names are never used as a substitute for a supplied source identity.

## Mapping policy

Only isomorphic or deliberately bounded mappings enter the semantic model. Unsupported semantics stay in `import_report.json` and are counted in `source_import.unsupported_or_lossy_count`.

This is important for audit interpretation: a green target verdict cannot compensate for a semantic rule that was already lost during source import.

See `V0_11_EXTERNAL_AUDIT_AND_COUNTEREXAMPLES.md` for the supported/gapped construct list and the public Factum schema used as the implementation reference.
