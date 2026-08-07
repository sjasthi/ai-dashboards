"""Low-level raw-row scanning, shared by workbook_probe.py and data_loader.py.

Both modules need the same answer to "which rows in this sheet actually hold
data" - workbook_probe so the upload-preview row count agrees with what the
analysis pipeline will load, data_loader so it hands pandas the right header
row instead of always trusting row 0. Computing it once here, from a single
pass over the same raw cell values, is what keeps the two from ever disagreeing
- see workbook_probe.py's module docstring for why that agreement matters.

No import of data_loader.py or workbook_probe.py here: workbook_probe already
imports DataLoader, so this stays a leaf module to avoid a cycle.
"""

from collections import namedtuple

ScanResult = namedtuple("ScanResult", ["header_row", "last_row", "width"])


def _blank(value):
    """Same test _extent has always used: None or '' is blank, whitespace is
    data - a cell of '   ' is still a value to pandas, so it must count as one
    here too or this scan and the loaded DataFrame disagree."""
    return value is None or value == ""


def scan_rows(rows):
    """One pass over a sheet's raw row tuples (openpyxl's
    `iter_rows(values_only=True)` or xlrd's `row_values` per row).

    Returns a ScanResult:
    - header_row: 1-based index of the first row holding any non-blank cell.
      0 if every row scanned is fully blank (an empty or header-only sheet).
    - last_row: 1-based index of the last row holding any non-blank cell.
      0 if the sheet is fully blank.
    - width: the widest row's count of filled cells, up to and including its
      last non-blank cell.

    A single streaming pass so callers reading via openpyxl's read_only mode
    (which can only be iterated once) still get both answers for free.
    """
    header_row = 0
    last_row = 0
    width = 0

    for i, row in enumerate(rows, start=1):
        filled = 0
        for j, value in enumerate(row, start=1):
            if not _blank(value):
                filled = j
        if filled:
            if header_row == 0:
                header_row = i
            last_row = i
            width = max(width, filled)

    return ScanResult(header_row=header_row, last_row=last_row, width=width)
