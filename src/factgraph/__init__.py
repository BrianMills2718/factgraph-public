"""factgraph: a fact-oriented semantic compiler."""

__version__ = "0.18.0"

from .model import (
    Model,
    EntityType,
    ValueType,
    ObjectifiedFactType,
    FactType,
    Role,
    Reading,
    Constraint,
    SampleFact,
)
from .parser import parse_model
from .normalize import normalize_model
from .diff import MigrationHints, semantic_diff
from .migration import plan_semantic_migration
from .merge import semantic_merge
from .metamodel import build_metamodel, encode_model as encode_metamodel_population, decode_model as decode_metamodel_population

__all__ = [
    "__version__",
    "Model",
    "EntityType",
    "ValueType",
    "ObjectifiedFactType",
    "FactType",
    "Role",
    "Reading",
    "Constraint",
    "SampleFact",
    "parse_model",
    "normalize_model",
    "MigrationHints",
    "semantic_diff",
    "plan_semantic_migration",
    "semantic_merge",
    "build_metamodel",
    "encode_metamodel_population",
    "decode_metamodel_population",
]
