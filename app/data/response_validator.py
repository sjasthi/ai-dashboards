from typing import Set

from pydantic import ValidationError

from .models import RecommendationsResponse


def parse_and_validate(raw: dict, valid_filenames: Set[str]) -> RecommendationsResponse:
    """
    Validate a raw LLM response dict against RecommendationsResponse, then
    cross-check every operation's files_involved against the files actually
    uploaded (Pydantic's List[str] typing alone can't catch a hallucinated
    filename - any string satisfies it).

    Raises:
        ValueError: If the response fails Pydantic validation or references
            an unknown filename. The message is written to be fed back to the
            LLM as correction text.
    """
    try:
        parsed = RecommendationsResponse.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"Response did not match the required schema:\n{e}") from e

    unknown_files = set()
    for rec in parsed.recommendations:
        for op in rec.required_operations:
            for filename in op.files_involved:
                if filename not in valid_filenames:
                    unknown_files.add(filename)

    if unknown_files:
        raise ValueError(
            f"required_operations referenced file(s) not among the uploaded files "
            f"{sorted(valid_filenames)}: {sorted(unknown_files)}"
        )

    return parsed
