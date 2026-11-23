"""Stable source citations for generated opportunity copy, without fuzzy quotes."""

import copy
import re


def citation_schema(schema: dict, citations: dict[str, str]) -> dict:
    """Constrain evidence to existing source IDs, without repeating their enum."""
    result = copy.deepcopy(schema)
    result["$defs"] = {"source_reference": {"type": "string", "enum": list(citations)}}

    def visit(node):
        if not isinstance(node, dict):
            return
        for key, child in node.get("properties", {}).items():
            if key in {"evidence", "application_evidence"} and child.get("type") == "array":
                child["items"] = {"$ref": "#/$defs/source_reference"}
            else:
                visit(child)
        if node.get("type") == "array":
            visit(node.get("items"))

    visit(result)
    result["properties"]["classification"]["properties"]["evidence"]["minItems"] = 1
    return result


def numbered_sources(sources: list[dict]) -> tuple[str, dict[str, str]]:
    sections, citations = [], {}
    for source_index, source in enumerate(sources, 1):
        # Keep all source words while limiting reference-enum size for tall
        # screenshots with many short OCR lines. Whitespace is immaterial to
        # the exact source validator; no facts are shortened or discarded.
        passages, pending = [], []
        for line in str(source.get("text", "")).splitlines():
            if line.strip():
                pending.append(line.strip())
            if len(" ".join(pending)) >= 200:
                passages.append(" ".join(pending)); pending = []
        if pending:
            passages.append(" ".join(pending))
        lines = [f"Source URL: {source['url']}", *passages]
        numbered = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            reference = f"[[S{source_index}:L{len(numbered) + 1}]]"
            citations[reference] = line
            numbered.append(f"{reference} {line}")
        sections.append("\n".join(numbered))
    return "\n\n".join(sections), citations


def resolve_citations(document: dict, citations: dict[str, str]) -> list[dict]:
    """Replace valid citation IDs with the actual source text before validation.

    Legacy exact quotations remain supported. Invented or malformed IDs remain
    unmatched and will be rejected by the existing evidence validator.
    """
    problems = []

    def visit(value, path=""):
        if isinstance(value, dict):
            for key, child in value.items():
                field_path = f"{path}.{key}".lstrip(".")
                if key in {"evidence", "application_evidence"} and isinstance(child, list):
                    resolved = []
                    for quote in child:
                        if quote in citations:
                            resolved.append(citations[quote])
                        else:
                            resolved.append(quote)
                            if re.search(r"\[\[S\d+:L", str(quote)):
                                problems.append({"field": field_path, "reason": "unknown_source_reference", "citation": str(quote)[:300]})
                    value[key] = resolved
                else:
                    visit(child, field_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(document)
    return problems[:30]
