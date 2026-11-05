"""Deterministic enrichment for stored SofaScore narrative comments.

The package deliberately has no database, network, or provider dependencies. It
classifies the original SofaScore type and assigns roles only from structured
participant slots. Text extraction can be added as a separate layer later.
"""

from .normalizer import enrich_comment
from .taxonomy import CATEGORY_BY_TYPE, ROLE_BY_TYPE, SUPPORTED_TYPES

__all__ = ["CATEGORY_BY_TYPE", "ROLE_BY_TYPE", "SUPPORTED_TYPES", "enrich_comment"]
