"""
JSON schemas for agent outputs (Anthropic structured outputs).

Structured outputs require every object to list all properties as required and
set additionalProperties to false. Optional information is expressed as empty
strings or empty arrays rather than missing keys.
"""
from __future__ import annotations


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_STR = {"type": "string"}
_CONF = {"type": "string", "enum": ["low", "medium", "high"]}

CITATION = _obj({
    "experiment_id": {"type": "integer"},
    "metric": _STR,
    "value": {"type": "number"},
})

FINDING = _obj({
    "title": _STR,
    "detail": _STR,
    "confidence": _CONF,
    "evidence": {"type": "array", "items": CITATION},
})

EXPERIMENT_PROPOSAL = _obj({
    "strategy_key": _STR,
    "universe": _STR,                      # preset name (us_large / uk_large) or comma-separated symbols
    "period": {"type": "string", "enum": ["train", "validation"]},
    "params": {"type": "array", "items": _obj({"name": _STR, "value": _STR})},
    "hypothesis": _STR,
    "rationale": _STR,
    "priority": _CONF,
})

CHANGE_PROPOSAL = _obj({
    "kind": {"type": "string", "enum": ["strategy_change", "risk_limit_change", "promote_to_paper"]},
    "title": _STR,
    "description": _STR,
    "rationale": _STR,
})

SPECIALIST = _obj({
    "summary": _STR,
    "findings": {"type": "array", "items": FINDING},
    "proposed_experiments": {"type": "array", "items": EXPERIMENT_PROPOSAL},
    "proposed_changes": {"type": "array", "items": CHANGE_PROPOSAL},
    "limitations": {"type": "array", "items": _STR},
})

DIRECTOR = _obj({
    "summary": _STR,
    "selected_proposals": {"type": "array", "items": _obj({"proposal_id": {"type": "integer"}, "reason": _STR})},
    "rejected_proposals": {"type": "array", "items": _obj({"proposal_id": {"type": "integer"}, "reason": _STR})},
    "next_priorities": {"type": "array", "items": _STR},
    "notes_for_human": _STR,
})
