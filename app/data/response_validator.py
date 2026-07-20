from typing import Dict, List, Optional, Set

import pandas as pd
from pydantic import ValidationError

from .models import RecommendationsResponse
from .report_builder import _normalize_join_keys
from .summary_builder import _comparable_value_sets

# Below this share of overlapping values, a proposed join key is treated as not a
# real relationship. Matches the threshold detect_relationships uses to discard a
# name match with no value evidence.
MIN_JOIN_OVERLAP = 0.05


def _verify_joins(
    parsed: RecommendationsResponse,
    tables: Dict[str, pd.DataFrame]
) -> List[str]:
    """Check each proposed join against the real data, returning a problem
    description per unusable join.

    The LLM is allowed to infer joins the profiling heuristics missed (natural keys
    like "account" don't look key-like by name), so its proposals have to be checked
    rather than trusted: a hallucinated key doesn't fail loudly, it silently produces
    a wrong-but-plausible number or an inner join that collapses to no rows.

    Deliberately conservative - only definite evidence of a bad join is reported, and
    anything that can't be checked is left alone.
    """
    problems = []

    for rec in parsed.recommendations:
        for op in rec.required_operations:
            if op.operation_type != "join":
                continue

            known = [f for f in op.files_involved if f in tables]
            if len(known) < 2:
                continue  # joining onto a prior step's result - can't check statically

            left_name, right_name = known[0], known[1]
            left, right = tables[left_name], tables[right_name]

            # _normalize_join_keys expects the raw JSON shapes (str / dict), but by
            # this point Pydantic has turned the {"left","right"} form into
            # JoinKeyPair instances - unwrap them or those keys go unchecked.
            raw_keys = [k if isinstance(k, str) else k.model_dump() for k in op.join_keys]

            for left_col, right_col in _normalize_join_keys(raw_keys):
                if left_col not in left.columns:
                    problems.append(
                        f'"{rec.report_name}": join key "{left_col}" does not exist in '
                        f'{left_name} (its columns are {list(left.columns)})'
                    )
                    continue
                if right_col not in right.columns:
                    problems.append(
                        f'"{rec.report_name}": join key "{right_col}" does not exist in '
                        f'{right_name} (its columns are {list(right.columns)})'
                    )
                    continue

                try:
                    values_left, values_right = _comparable_value_sets(
                        left[left_col], right[right_col]
                    )
                except Exception:
                    continue  # unable to compare - don't block on it

                if not values_left or not values_right:
                    continue

                overlap = len(values_left & values_right) / min(len(values_left), len(values_right))
                if overlap < MIN_JOIN_OVERLAP:
                    problems.append(
                        f'"{rec.report_name}": {left_name}."{left_col}" and '
                        f'{right_name}."{right_col}" share only {overlap:.1%} of their '
                        f"values, so they are not a real join key. Use a column pair that "
                        f"actually shares values, or drop the join and recommend a "
                        f"single-file report instead."
                    )

    return problems


def parse_and_validate(
    raw: dict,
    valid_filenames: Set[str],
    tables: Optional[Dict[str, pd.DataFrame]] = None
) -> RecommendationsResponse:
    """
    Validate a raw LLM response dict against RecommendationsResponse, then
    cross-check every operation's files_involved against the files actually
    uploaded (Pydantic's List[str] typing alone can't catch a hallucinated
    filename - any string satisfies it).

    When `tables` is given, proposed joins are additionally verified against the
    real data (see _verify_joins).

    Raises:
        ValueError: If the response fails Pydantic validation, references
            an unknown filename, or proposes an unusable join. The message is
            written to be fed back to the LLM as correction text.
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

    if tables:
        join_problems = _verify_joins(parsed, tables)
        if join_problems:
            raise ValueError(
                "These joins were checked against the actual data and are not usable:\n"
                + "\n".join(f"- {p}" for p in join_problems)
            )

    return parsed
