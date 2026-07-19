"""Single source of truth for the qualitative-v2 wire contract."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping

SCHEMA_VERSION: Final = "qualitative-score-v2"
RUBRIC_VERSION: Final = "rubric-v1"
TAXONOMY_VERSION: Final = "taxonomy-v1"

DIMENSION_NAMES: Final = ("moat", "market_pos", "sentiment")
DIMENSION_STATUS_VALUES: Final = ("scored", "insufficient_data")
OVERALL_STATUS_VALUES: Final = ("scored", "insufficient_data")
CONFIDENCE_VALUES: Final = ("low", "medium", "high")
SCORE_RANGES: Final[Mapping[str, tuple[int, int]]] = MappingProxyType(
    {"moat": (1, 10), "market_pos": (1, 5), "sentiment": (1, 5)}
)

RATIONALE_MAX_CHARS: Final = 500
EVIDENCE_IDS_MAX_ITEMS: Final = 64
EVIDENCE_PACKET_MAX_ITEMS: Final = 64
EVIDENCE_ID_MAX_CHARS: Final = 128
EVIDENCE_VALUE_MAX_JSON_BYTES: Final = 4096
SOURCE_MAX_CHARS: Final = 512
UNIT_MAX_CHARS: Final = 64
CONTEXT_TEXT_MAX_CHARS: Final = 256
PROMPT_PACKET_MAX_BYTES: Final = 131_072


__all__ = [
    "CONFIDENCE_VALUES",
    "CONTEXT_TEXT_MAX_CHARS",
    "DIMENSION_NAMES",
    "DIMENSION_STATUS_VALUES",
    "EVIDENCE_ID_MAX_CHARS",
    "EVIDENCE_IDS_MAX_ITEMS",
    "EVIDENCE_PACKET_MAX_ITEMS",
    "EVIDENCE_VALUE_MAX_JSON_BYTES",
    "OVERALL_STATUS_VALUES",
    "PROMPT_PACKET_MAX_BYTES",
    "RATIONALE_MAX_CHARS",
    "RUBRIC_VERSION",
    "SCHEMA_VERSION",
    "SCORE_RANGES",
    "SOURCE_MAX_CHARS",
    "TAXONOMY_VERSION",
    "UNIT_MAX_CHARS",
]
