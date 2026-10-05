# External semantic imports

Factgraph's v0.10 strategy is to audit models people already have. The in-house DSL is a fixture/debug format, not an adoption prerequisite.

## Apache Ossie

Command:

```bash
factgraph import-ossie ontology.yaml --out-dir artifacts/import
factgraph audit ontology.yaml --out-dir artifacts/audit
```

The bounded importer currently preserves selected EntityType/ValueType concepts, relationships and named roles, readings, simple identification, and single inheritance. Source-stable identities use an `ossie:` namespace.

Unsupported or ambiguous constructs—such as expressions the semantic kernel cannot represent faithfully—are reported in `import_report.json`; they are not silently dropped or reinterpreted.

## LinkML

Command:

```bash
factgraph import-linkml schema.yaml --out-dir artifacts/import
factgraph audit schema.yaml --out-dir artifacts/audit
```

The bounded importer currently handles:

- classes;
- scalar slots;
- enums/permissible values;
- identifier/required scalar fields;
- scalar min+max ranges;
- single `is_a` inheritance;
- class-valued slots as explicit facts;
- multivalued slots with finite bounds where the Factgraph frequency contract aligns.

It explicitly reports gaps for patterns, mixins, rules/classification rules, complex/alternate uniqueness, class-valued identifiers, dynamic/composed enums, and other unsupported semantics.

Source-stable identities use a `linkml:` namespace.

## Auto-detection

For YAML/JSON, `factgraph audit --input-format auto` examines structure:

- `ontology` list -> Ossie;
- `classes`/`slots`/`enums` -> LinkML;
- ambiguous or unrecognized input -> error.

File extension alone never decides between the two.

## Next priority

Factum/FBM-family ORM input is the next interoperability target. The goal is to consume existing rich ORM authoring ecosystems, not recreate their editors/verbalizers.
