"""Stage versions replace hashing every source file.

Bump the relevant number when that stage's outputs change meaning.
Editing a viewer or a comment does not invalidate checkpoints or caches.
"""

STAGE_VERSIONS = {
    "discovery": 8,
    "semantic_cards": 2,
    "quantity_registry": 4,
    "dimension_members": 7,
    "structural_relations": 5,
    "group_incremental": 11,
    "table_keys": 2,
    "profiles": 1,
    "association_rules": 1,
    "instance_bundles": 1,
}
