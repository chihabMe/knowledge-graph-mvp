"""Ontology as code.

Mirrors AGENT_PROJECT_BRIEF.md section 8 exactly. That document is the source
of truth (see AGENTS.md "Source Of Truth") — if a feature needs a new entity
or relationship type, update the brief first, then this module, in the same
change. Do not add types here that aren't in the brief.
"""

ENTITY_TYPES = frozenset(
    {
        "Document",
        "Person",
        "Project",
        "Customer",
        "Organization",
        "Procedure",
        "Machine",
        "Part",
        "Vendor",
        "Policy",
        "Task",
        "Topic",
    }
)

RELATIONSHIP_TYPES = frozenset(
    {
        "mentions",
        "authored",
        "responsible_for",
        "references",
        "supersedes",
        "belongs_to",
        "depends_on",
        "works_on",
        "owns",
        "related_to",
    }
)


class UnknownEntityTypeError(ValueError):
    pass


class UnknownRelationshipTypeError(ValueError):
    pass


def validate_entity_type(entity_type: str) -> None:
    if entity_type not in ENTITY_TYPES:
        raise UnknownEntityTypeError(entity_type)


def validate_relationship_type(relationship_type: str) -> None:
    if relationship_type not in RELATIONSHIP_TYPES:
        raise UnknownRelationshipTypeError(relationship_type)
