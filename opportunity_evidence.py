"""Stable source citations for generated opportunity copy, without fuzzy quotes."""

import re


def numbered_sources(sources: list[dict]) -> tuple[str, dict[str, str]]:
    sections, citations = [], {}
    for source_index, source in enumerate(sources, 1):
        lines = [f"Source URL: {source['url']}", *str(source.get("text", "")).splitlines()]
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
