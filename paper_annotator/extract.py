"""Ask Claude to find each configured field in a paper and return verbatim quotes."""

import anthropic
from pydantic import BaseModel, ValidationError

from .config import Config

SYSTEM = """You extract specific information from research papers for a literature review.

You will get the text of one paper, split into pages marked "=== Page N ===", and a list of fields.
For every field:
- value: a short, self-contained answer written by you (1-3 sentences), based only on what the
  paper explicitly states. If the paper does not state this information, return an empty string
  "" and no quotes. Do not infer, estimate, guess, or fill in from general knowledge, and do not
  write placeholders such as "Not reported" or "N/A" — leave it blank.
- quotes: the passages in the paper that state this information, so they can be highlighted.
  Copy each quote EXACTLY, word for word, from the page text — do not paraphrase, fix typos,
  or join text from different places. Each quote should be one sentence or a short run of
  sentences (under ~60 words). Include the distinct passages needed to capture the important
  evidence for the field. Usually return 1-3 quotes; use up to the configured limit when each
  adds a different useful detail. Do not repeat the same fact, highlight generic background, or
  add quotes just to fill the limit. Prefer relevant evidence from the body; include the abstract
  only if it adds something the body does not. Give the page number the quote appears on.

Return every field from the list exactly once, using the field names as given."""


class Quote(BaseModel):
    page: int
    text: str


class FieldResult(BaseModel):
    field: str
    value: str
    quotes: list[Quote]


class Extraction(BaseModel):
    fields: list[FieldResult]


class ExtractionError(Exception):
    pass


def extract(client: anthropic.Anthropic, cfg: Config, pages: list[str]) -> Extraction:
    field_list = "\n".join(
        f"- {f.name}: {f.description} "
        + (f"(at most {cfg.max_quotes_per_field} quotes)" if f.color else "(summary only: no quotes)")
        for f in cfg.fields
    )
    system = f"{SYSTEM}\n\n<review_context>\n{cfg.context}\n</review_context>" if cfg.context else SYSTEM
    paper = "\n\n".join(f"=== Page {i + 1} ===\n{text}" for i, text in enumerate(pages))
    # Haiku has no server-side fallback; on other models, a declined paper is retried on a fallback model.
    fallback = {} if "haiku" in cfg.model else {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    # Streaming allows a large max_tokens (thinking + 17 fields of quotes) without HTTP timeouts.
    try:
        with client.beta.messages.stream(
            model=cfg.model,
            max_tokens=64000,
            system=system,
            messages=[{"role": "user", "content": f"<paper>\n{paper}\n</paper>\n\nFields to extract:\n{field_list}"}],
            output_config={"effort": cfg.effort},
            output_format=Extraction,
            **fallback,
        ) as stream:
            response = stream.get_final_message()
    except ValidationError as e:
        # The JSON is parsed as soon as the answer ends, so a truncated answer fails here.
        raise ExtractionError("Claude's answer was cut off or malformed; try again or lower max_quotes_per_field") from e
    if response.stop_reason == "refusal":
        raise ExtractionError("Claude declined to process this paper")
    if response.stop_reason == "max_tokens":
        raise ExtractionError("Response was cut off (max_tokens); lower max_quotes_per_field or effort")
    if response.parsed_output is None:
        raise ExtractionError("Claude returned no structured output")
    result = response.parsed_output
    for fr in result.fields:
        if _is_blank(fr.value):
            fr.value, fr.quotes = "", []
        else:
            field = cfg.field(fr.field)
            if field is None or field.color is None:
                fr.quotes = []
            else:
                fr.quotes = fr.quotes[:cfg.max_quotes_per_field]
    return result


_PLACEHOLDERS = {"", "not reported", "not stated", "not specified", "not mentioned", "not available",
                 "n/a", "na", "none", "unknown", "unclear", "-", "—"}


def _is_blank(value: str) -> bool:
    return value.strip().strip(".").lower() in _PLACEHOLDERS
