import re
from typing import Dict, List, Optional, Set

import pandas as pd
from pydantic import ValidationError

from .models import RecommendationsResponse
from .report_builder import _infer_groupby_columns, _normalize_join_keys
from .summary_builder import _comparable_value_sets

# Below this share of overlapping values, a proposed join key is treated as not a
# real relationship. Matches the threshold detect_relationships uses to discard a
# name match with no value evidence.
MIN_JOIN_OVERLAP = 0.05


def _canonical_key(filename: str) -> str:
    """Normalized filename for tolerant matching - case, parentheses, and the
    difference between spaces, underscores and hyphens, are ignored.

    Worksheet-derived names carry parentheses ("orders (workbook 02).xlsx") and the
    LLM routinely drops one - typically the closing paren before the extension - so
    strip them before comparing rather than failing an otherwise-unambiguous match.
    """
    cleaned = filename.strip().lower().replace("(", "").replace(")", "")
    return re.sub(r"[\s_-]+", " ", cleaned).strip()


def _canonicalize_filenames(
    parsed: RecommendationsResponse,
    valid_filenames: Set[str]
) -> Set[str]:
    """Rewrite near-miss filenames to the real uploaded name, returning any that still
    don't resolve.

    Worksheet-derived names contain spaces ("sales pipeline (workbook).xlsx") and the
    LLM routinely writes them with underscores instead. That one-character difference
    used to fail validation and force an entire retry, so repair the near-miss here
    rather than spending another round trip on it. Only an unambiguous match is
    accepted - if two uploaded files normalize the same way, we can't safely guess.
    """
    by_key: Dict[str, List[str]] = {}
    for name in valid_filenames:
        by_key.setdefault(_canonical_key(name), []).append(name)

    unknown = set()
    for rec in parsed.recommendations:
        for op in rec.required_operations:
            resolved = []
            for filename in op.files_involved:
                if filename in valid_filenames:
                    resolved.append(filename)
                    continue

                matches = by_key.get(_canonical_key(filename), [])
                if len(matches) == 1:
                    print(f"[Validator] Repaired filename {filename!r} -> {matches[0]!r}")
                    resolved.append(matches[0])
                else:
                    # unknown, or ambiguous between two real files
                    resolved.append(filename)
                    unknown.add(filename)
            op.files_involved = resolved

    return unknown


def _check_groupby_columns(parsed: RecommendationsResponse) -> List[str]:
    """Report every groupby step that names no column to group by and whose key can't
    be recovered from the rest of its recommendation.

    "groupby_columns": [] is schema-valid (a List[str] with a default), so Pydantic
    accepts it and the failure only surfaces much later, as a raw "No groupby columns
    specified" on the user's report card. Catching it here instead puts it in the
    correction text sent back to the LLM, where it can still be fixed.

    Steps whose key report_builder can infer are deliberately NOT reported: that repair
    is deterministic and free, and spending a retry (a full extra generation) on a
    report that will build correctly anyway is exactly the quota this validator's
    filename repair already exists to save.
    """
    problems = []

    for rec in parsed.recommendations:
        rec_dict = rec.model_dump()
        for op in rec_dict.get("required_operations", []):
            if op.get("operation_type") != "groupby" or op.get("groupby_columns"):
                continue
            if not op.get("aggregations"):
                continue  # a wholly empty groupby is a no-op the report builder skips
            if _infer_groupby_columns(rec_dict, op):
                continue

            problems.append(
                f'"{rec.report_name}": a "groupby" step has an empty "groupby_columns", '
                f"so there is nothing to group by and the report cannot be built. Name the "
                f"categorical column this report breaks its measure down by, and make sure "
                f"that same column also appears in expected_output_schema and as "
                f"plotly_config.x_axis."
            )

    return problems


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
    filename - any string satisfies it). Filenames that differ only by case or by
    space/underscore/hyphen are repaired in place rather than rejected.

    Groupby steps are checked for an empty groupby_columns (see
    _check_groupby_columns), and when `tables` is given, proposed joins are
    additionally verified against the real data (see _verify_joins).

    Returns the validated response with filenames canonicalized to the real
    uploaded names, so downstream lookups by filename resolve.

    Raises:
        ValueError: If the response fails Pydantic validation, references
            an unknown filename, proposes an unusable join, or contains a groupby
            with no usable group key. The message is written to be fed back to the
            LLM as correction text.
    """
    try:
        parsed = RecommendationsResponse.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"Response did not match the required schema:\n{e}") from e

    unknown_files = _canonicalize_filenames(parsed, valid_filenames)

    if unknown_files:
        raise ValueError(
            f"required_operations referenced file(s) not among the uploaded files "
            f"{sorted(valid_filenames)}: {sorted(unknown_files)}"
        )

    groupby_problems = _check_groupby_columns(parsed)
    if groupby_problems:
        raise ValueError(
            "These groupby steps do not name a column to group by:\n"
            + "\n".join(f"- {p}" for p in groupby_problems)
        )

    if tables:
        join_problems = _verify_joins(parsed, tables)
        if join_problems:
            raise ValueError(
                "These joins were checked against the actual data and are not usable:\n"
                + "\n".join(f"- {p}" for p in join_problems)
            )

    return parsed
