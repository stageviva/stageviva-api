from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from openai import OpenAI

from artist_schema import ARTIST_SCHEMA


# Structured CV extraction needs fast, reliable JSON rather than long-form
# reasoning. It can be overridden in deployment after measured evaluation.
ARTIST_ANALYSIS_MODEL = os.getenv("STAGEVIVA_ARTIST_ANALYSIS_MODEL", "gpt-4o-mini")
OPENAI_REQUEST_TIMEOUT_SECONDS = float(
    os.getenv("STAGEVIVA_OPENAI_TIMEOUT_SECONDS", "75")
)


def _reasoning_options() -> dict[str, Any]:
    """GPT-4.1 does not accept the GPT-5 reasoning control parameter."""
    return {"reasoning": {"effort": "none"}} if ARTIST_ANALYSIS_MODEL.startswith("gpt-5") else {}


def _analysis_client() -> OpenAI:
    """Use a bounded request so a CV job cannot appear to run forever."""
    return OpenAI(timeout=OPENAI_REQUEST_TIMEOUT_SECONDS, max_retries=1)


# ============================================================
# ERRORS
# ============================================================

class ArtistAnalysisError(RuntimeError):
    pass


# ============================================================
# EXTRACT CV TEXT
# ============================================================

def extract_cv_text(
    file_path: str,
    max_characters: int = 45_000,
) -> str:
    """
    Extract readable text from a CV file.

    Currently supports:
    - PDF
    - DOCX
    - TXT
    """

    path = Path(file_path)

    if not path.exists():
        raise ArtistAnalysisError(
            f"CV file not found: {file_path}"
        )

    suffix = path.suffix.lower()

    # --------------------------------------------------------
    # TXT
    # --------------------------------------------------------

    if suffix == ".txt":
        text = path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

        return text[:max_characters]

    # --------------------------------------------------------
    # PDF
    # --------------------------------------------------------

    if suffix == ".pdf":

        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise ArtistAnalysisError(
                "PDF support requires pypdf. "
                "Install it with: pip install pypdf"
            ) from error

        try:
            reader = PdfReader(str(path))

            pages = []

            for page in reader.pages:
                page_text = page.extract_text()

                if page_text:
                    pages.append(page_text)

            text = "\n".join(pages)

        except Exception as error:
            raise ArtistAnalysisError(
                f"Could not read PDF CV: {error}"
            ) from error

        return text[:max_characters]

    # --------------------------------------------------------
    # DOCX
    # --------------------------------------------------------

    if suffix == ".docx":

        try:
            from docx import Document
        except ImportError as error:
            raise ArtistAnalysisError(
                "DOCX support requires python-docx. "
                "Install it with: pip install python-docx"
            ) from error

        try:
            document = Document(str(path))

            paragraphs = [
                paragraph.text.strip()
                for paragraph in document.paragraphs
                if paragraph.text.strip()
            ]

            text = "\n".join(paragraphs)

        except Exception as error:
            raise ArtistAnalysisError(
                f"Could not read DOCX CV: {error}"
            ) from error

        return text[:max_characters]

    raise ArtistAnalysisError(
        f"Unsupported CV format: {suffix}. "
        "Use PDF, DOCX or TXT."
    )


# ============================================================
# OPENAI INSTRUCTIONS
# ============================================================

ANALYSIS_INSTRUCTIONS = """
You are StageViva's Artist Intelligence Engine.

Your job is to analyse ONE dancer's CV and create a structured
representation of the dancer's professional profile.

The output will later be compared against StageViva
Opportunity Intelligence.

Your goal is NOT to make the dancer sound more impressive.

Your goal is:

"Based on the available evidence, what does this dancer
actually demonstrate?"

Keep the response concise. Artist DNA is for matching, not a full CV archive:
select the strongest and most relevant credits, training and evidence rather
than listing every item.

============================================================
CORE PRINCIPLES
============================================================

1. NEVER INVENT FACTS.

Only use information supported by the supplied CV.

If something is not stated:

use "unknown"

or:

[]

depending on the field.

Do not fill gaps using assumptions.

============================================================
2. SOURCE-GROUNDED ANALYSIS
============================================================

The CV text supplied to you is the factual source.

Do not use general knowledge to invent:

- experience
- qualifications
- roles
- companies
- dates
- nationality
- work rights
- languages
- physical characteristics
- dance levels

Everything must be supported by the CV.

============================================================
3. DISCIPLINE ANALYSIS
============================================================

Identify the performance disciplines demonstrated by the artist.

Possible disciplines include:

- classical_ballet
- contemporary
- jazz
- commercial
- hip_hop
- musical_theatre
- tap
- ballroom
- latin
- character
- folk
- acrobatics
- circus
- partnering
- improvisation
- singing
- acting

You may also identify another discipline when it is clearly
supported by the CV.

The order matters. List the artist's central professional identity first,
then supporting techniques. Use professional credits, stated performer type,
and named professional skills before a teaching qualification or a single
training line. For example, an artist with musical-theatre credits and
professional singing evidence is a musical-theatre / singing performer even
if the CV also lists classical ballet training. Do not make ballet the lead
discipline solely because it appears as a qualification.

Actively extract singing and acting when the CV gives direct evidence such as
credited singer / actor roles, a stated professional level, vocal range,
musical-theatre credits, or an explicitly named acting skill. Do not omit a
clearly evidenced performance discipline just because the CV is dance-led.

CLASSICAL BALLET LEVEL POLICY:
- "high" or "very_high" is reserved for a professional classical-ballet
  career: named employment with a ballet company, or substantial professional
  classical repertoire / rank evidence.
- A skills list, a ballet class, an exam, teacher training, or useful ballet
  technique alone is supporting technique and must be "medium" or lower.
- Do not label a musical-theatre, commercial, or contemporary performer as a
  high-level classical-ballet artist solely because ballet is listed among
  their skills. Their primary disciplines must reflect their professional
  credits and performer identity.

============================================================
4. DISCIPLINE LEVELS
============================================================

For each discipline that is supported by the CV, estimate the
dancer's demonstrated level.

Use ONLY:

very_high
high
medium
low
unknown

This is an assessment of the ARTIST.

It is NOT an assessment of an opportunity.

Use evidence such as:

- professional company experience
- professional roles
- advanced training
- major repertoire
- repeated professional performance
- specialist experience
- competition results
- qualifications

Be conservative.

Do not assign very_high simply because a discipline appears
on the CV.

============================================================
5. PROFESSIONAL EXPERIENCE
============================================================

Extract:

- professional companies
- roles
- ranks
- employment
- freelance work
- performance work
- relevant dates
- locations

Distinguish professional experience from education.

PROFESSIONAL CREDIT PRIORITY:
- Preserve every recent, current, forthcoming, or especially significant
  professional credit that names a production, role and organisation.
- Ensemble, featured, cover and understudy responsibilities are professional
  credits; retain every responsibility stated in the role.
- Work with a professional cruise, theatre, production, dance or entertainment
  company is professional experience when the CV identifies it as such.
- For each retained production credit, create both a
  career.professional_experience entry and a repertoire entry where applicable.
- Never replace a named company and production with a generic summary, or
  drop a major credit because the CV also contains more dance work.

============================================================
6. REPERTOIRE
============================================================

Extract repertoire when explicitly stated.

For each work identify:

- work
- role
- choreographer
- company
- year

Do not invent missing information.

============================================================
7. TRAINING
============================================================

Extract:

- institution
- programme
- qualification
- dates
- current status

Do not automatically classify an institution as prestigious
unless the supplied CV itself provides evidence for that
classification.

If reputation cannot be established from the supplied source,
use:

"unknown"

============================================================
8. TEACHING
============================================================

Extract teaching experience and qualifications only when
explicitly supported.

============================================================
9. CHOREOGRAPHY
============================================================

Extract choreography experience when stated.

============================================================
10. CAPABILITIES
============================================================

Extract relevant capabilities such as:

- partnering
- pointe
- pas de deux
- improvisation
- acting
- singing
- choreography
- teaching
- specific dance techniques
- other clearly evidenced professional skills

Do not invent skill levels.

============================================================
11. LANGUAGES
============================================================

Only record languages explicitly stated in the CV.

============================================================
12. ELIGIBILITY
============================================================

Only record work rights or visa information if explicitly
provided.

Do not infer citizenship or work rights from a location.

============================================================
13. ACHIEVEMENTS
============================================================

Extract competitions, awards, scholarships and other
achievements when explicitly stated.

============================================================
14. PREFERENCES
============================================================

Only populate preferences when they are explicitly stated in
the supplied CV.

Do not infer preferred countries, companies or roles simply
from previous experience.

============================================================
15. INTELLIGENCE
============================================================

Identify the dancer's strongest professionally relevant
characteristics based on evidence.

Examples:

- strong professional ballet experience
- extensive classical repertoire
- contemporary experience
- professional stage experience
- partnering experience

Every strength must be supported by evidence.

Also identify genuinely missing information that could be useful
for future opportunity matching.

Prioritise questions that materially decide whether an opportunity is viable:

- age or date of birth, only when age-restricted work is relevant
- current location and work authorisation
- availability for new work
- willingness to relocate
- preferred contract types and performer disciplines

Do NOT ask for detailed repertoire venues, individual production dates,
choreographers or training-course completion unless the missing fact directly
affects eligibility for a likely opportunity. Keep questions short and easy to
answer. Put availability in preferences.availability when it is confirmed.

============================================================
16. PHYSICAL INFORMATION
============================================================

Extract height if explicitly stated.

Do NOT infer height from photographs.

Do not infer physical characteristics from the dancer's name,
roles, nationality or company.

If a headshot is not supplied to this analysis:

headshot_available = false

============================================================
17. IDENTITY
============================================================

Extract:

- name
- age
- nationality
- location

Only when supported by the CV.

If not available:

"unknown"

============================================================
18. CURRENT STAGE
============================================================

Determine the current professional stage only from evidence
contained in the CV.

Examples:

- professional dancer
- emerging professional
- student
- graduate
- freelance dancer

Do not invent a stage that is not supported.

============================================================
19. TRAJECTORY
============================================================

Summarise the professional trajectory using only evidence from
the CV.

Examples:

- professional ballet training → professional company
- conservatoire training → freelance performance
- student → graduate → company dancer

Do not speculate about future career plans.

============================================================
20. EVIDENCE
============================================================

Evidence must come from the supplied CV.

Prefer short exact snippets.

Do not create fake quotations.

============================================================
21. FINAL OUTPUT
============================================================

Return ONLY structured JSON matching ARTIST_SCHEMA.

Do not return markdown.

Do not return explanations outside the JSON.
"""


# ============================================================
# ANALYSE ARTIST
# ============================================================

def analyse_artist(
    cv_path: str,
    client: Optional[OpenAI] = None,
) -> dict[str, Any]:
    """
    Extract a CV and analyse it into structured Artist DNA.
    """

    load_dotenv()

    cv_text = extract_cv_text(cv_path)

    if not cv_text.strip():
        raise ArtistAnalysisError(
            f"No usable text could be extracted from {cv_path}."
        )

    prompt = (
        f"CV FILE: {cv_path}\n\n"
        f"CV TEXT "
        f"(the only factual source for extraction):\n"
        f"{cv_text}"
    )

    active_client = client or _analysis_client()
    response = active_client.responses.create(
        model=ARTIST_ANALYSIS_MODEL,
        **_reasoning_options(),
        max_output_tokens=3_500,
        instructions=ANALYSIS_INSTRUCTIONS,
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "stageviva_artist",
                "strict": True,
                "schema": ARTIST_SCHEMA,
            }
        },
    )

    try:
        artist = json.loads(response.output_text)
        artist = _normalise_artist_disciplines(artist)
        artist = _supplement_explicit_performance_disciplines(artist, cv_text)
        artist = _cap_training_only_classical_ballet(artist)
        artist = _audit_artist_dna(artist, cv_text, active_client)
        artist = _normalise_artist_disciplines(artist)
        artist = _supplement_explicit_performance_disciplines(artist, cv_text)
        return _cap_training_only_classical_ballet(artist)

    except json.JSONDecodeError as error:
        raise ArtistAnalysisError(
            "OpenAI returned invalid structured output."
        ) from error


_DISCIPLINE_ALIASES = {
    "ballet": "classical_ballet",
    "classical ballet": "classical_ballet",
    "musical theatre": "musical_theatre",
    "musical theater": "musical_theatre",
    "hip hop": "hip_hop",
}


def _material_credit_lines(cv_text: str) -> list[str]:
    """Surface CV rows most likely to be silently lost during summarisation."""
    lines: list[str] = []
    role_markers = (
        "ensemble", "understudy", "cover ", "featured", "solo", "lead ",
        "principal", "singer", "dancer", "swing", "captain", "actor",
    )
    for raw_line in cv_text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        lowered = line.lower()
        if (
            re.search(r"\b(?:19|20)\d{2}\b", line)
            and any(marker in lowered for marker in role_markers)
        ):
            lines.append(line)
    return lines[:20]


def _audit_artist_dna(
    artist: dict[str, Any], cv_text: str, client: OpenAI,
) -> dict[str, Any]:
    """Run a second source-grounded pass before a profile can be stored.

    The first pass is optimised for clean extraction. This pass is deliberately
    adversarial: it checks that a concise summary did not discard a material
    professional credit, named employer, singer/actor evidence, or current
    performer identity visible in the source CV.
    """
    candidates = _material_credit_lines(cv_text)
    response = client.responses.create(
        model=ARTIST_ANALYSIS_MODEL,
        **_reasoning_options(),
        max_output_tokens=3_500,
        instructions="""
You are StageViva's Artist DNA quality-control pass.

Compare the draft Artist DNA with the source CV. Return a corrected COMPLETE
Artist DNA using the supplied JSON schema, not a patch.

Your task is to catch omissions before the performer sees their profile:
- Every material professional credit with a named production, role and company
  must appear in career.professional_experience and, if a production, repertoire.
- Keep all explicitly stated ensemble, featured, cover, understudy, swing,
  singer and actor responsibilities.
- Ensure the primary career identity and discipline ordering reflect credits,
  not just training.
- A professional singing statement, vocal range or credited singer role must
  be represented as singing; an explicit jazz credit or skill as jazz.
- Classical ballet may be high only for a demonstrated professional ballet
  career, never merely for training or an isolated skill listing.

Use the CV as the sole source. Preserve correct draft content; do not invent
facts. Return only JSON matching the schema.
""",
        input=(
            f"SOURCE CV:\n{cv_text}\n\n"
            f"MATERIAL CREDIT LINES TO CHECK:\n{json.dumps(candidates, ensure_ascii=False)}\n\n"
            f"DRAFT ARTIST DNA:\n{json.dumps(artist, ensure_ascii=False)}"
        ),
        text={
            "format": {
                "type": "json_schema",
                "name": "stageviva_artist_quality_checked",
                "strict": True,
                "schema": ARTIST_SCHEMA,
            }
        },
    )
    try:
        return json.loads(response.output_text)
    except json.JSONDecodeError as error:
        raise ArtistAnalysisError("OpenAI returned invalid quality-control output.") from error


def _normalise_artist_disciplines(artist: dict[str, Any]) -> dict[str, Any]:
    """Keep AI output in the shared matching vocabulary.

    The structured schema permits strings so a model can express an unfamiliar
    style. Familiar labels, however, must use the same keys as Opportunity
    Intelligence; otherwise a confirmed singer can look absent to matching.
    """
    disciplines = artist.get("disciplines")
    if not isinstance(disciplines, list):
        return artist

    for item in disciplines:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("discipline", "")).strip().lower()
        item["discipline"] = _DISCIPLINE_ALIASES.get(raw, raw.replace(" ", "_"))
    return artist


def _supplement_explicit_performance_disciplines(
    artist: dict[str, Any], cv_text: str,
) -> dict[str, Any]:
    """Retain unambiguous artist-declared core skills if a concise AI pass omits them.

    This is deliberately narrow: a discipline is added only where the CV calls
    the artist a professional singer or names a vocal range, or directly names
    jazz expertise. It prevents a dance-heavy layout from hiding material that
    is decisive for musical-theatre matching.
    """
    disciplines = artist.setdefault("disciplines", [])
    if not isinstance(disciplines, list):
        return artist

    existing = {
        str(item.get("discipline", "")).strip().lower()
        for item in disciplines if isinstance(item, dict)
    }
    compact_text = re.sub(r"\s+", " ", cv_text).lower()

    def add_if_missing(name: str, evidence: str) -> None:
        if name not in existing:
            disciplines.append({
                "discipline": name,
                "level": "high",
                "evidence": [evidence],
                "confidence": "high",
            })
            existing.add(name)

    if (
        "singer-professional" in compact_text
        or "singer professional" in compact_text
        or "vocal range" in compact_text
    ):
        add_if_missing("singing", "CV explicitly states professional singing ability or vocal range")

    if "jazz dancing" in compact_text or "jazz theatre company" in compact_text:
        add_if_missing("jazz", "CV explicitly lists jazz dancing or a jazz theatre credit")

    return artist


def _cap_training_only_classical_ballet(artist: dict[str, Any]) -> dict[str, Any]:
    """Do not mistake ballet training for a professional ballet career."""
    disciplines = artist.get("disciplines", [])
    if not isinstance(disciplines, list):
        return artist

    career_evidence = json.dumps(
        {"career": artist.get("career", {}), "repertoire": artist.get("repertoire", [])},
        ensure_ascii=False,
    ).lower()
    professional_ballet_role = any(marker in career_evidence for marker in (
        "company dancer", "principal", "soloist", "corps de ballet", "ballet dancer",
    ))
    named_ballet_context = any(marker in career_evidence for marker in (
        "ballet company", "ballet theatre", "ballet theater", "ballet cymru",
        "professional ballet",
    ))
    has_professional_ballet_career = professional_ballet_role and named_ballet_context

    for item in disciplines:
        if not isinstance(item, dict) or item.get("discipline") != "classical_ballet":
            continue
        if item.get("level") in {"high", "very_high"} and not has_professional_ballet_career:
            item["level"] = "medium"
            item["confidence"] = "medium"
    return artist


def enrich_artist_dna(
    artist_dna: dict[str, Any],
    answers: list[dict[str, str]],
    client: Optional[OpenAI] = None,
) -> dict[str, Any]:
    """Merge performer-confirmed answers into Artist DNA without inventing facts."""
    load_dotenv()
    response = (client or _analysis_client()).responses.create(
        model=ARTIST_ANALYSIS_MODEL,
        **_reasoning_options(),
        max_output_tokens=3_500,
        instructions=(
            "You update a performing artist's structured Artist DNA. Preserve all confirmed "
            "facts in the existing DNA unless a performer explicitly corrects them. Use only "
            "the performer answers to fill gaps; never infer or invent missing facts. Recompute "
            "intelligence.missing_information and intelligence.questions_to_ask, removing questions "
            "that have been answered and keeping only concise questions that materially improve matching."
        ),
        input=(
            "EXISTING ARTIST DNA:\n"
            f"{json.dumps(artist_dna, ensure_ascii=False)}\n\n"
            "PERFORMER-CONFIRMED ANSWERS:\n"
            f"{json.dumps(answers, ensure_ascii=False)}"
        ),
        text={
            "format": {
                "type": "json_schema",
                "name": "stageviva_artist_enriched",
                "strict": True,
                "schema": ARTIST_SCHEMA,
            }
        },
    )
    try:
        return json.loads(response.output_text)
    except json.JSONDecodeError as error:
        raise ArtistAnalysisError("OpenAI returned invalid structured output.") from error


# ============================================================
# COMMAND LINE
# ============================================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Analyse a StageViva dancer CV "
            "and create Artist DNA."
        )
    )

    parser.add_argument(
        "cv_path",
        help="Path to the dancer CV.",
    )

    parser.add_argument(
        "--output",
        default="artist_dna.json",
        help="Output JSON file.",
    )

    args = parser.parse_args()

    result = analyse_artist(
        args.cv_path
    )

    output_path = Path(args.output)

    output_path.write_text(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(
        f"Artist DNA created: {output_path}"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
