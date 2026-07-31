"""Metadata probe for uploaded spreadsheets.

The upload screen has to show what is inside a workbook - sheet names, row
counts - the moment files are picked, long before the analysis pipeline runs:

- .xlsx via openpyxl in read_only mode, which streams rows rather than building
  the whole workbook in memory.
- .xls via xlrd with on_demand, so unopened sheets are never materialised.
- .csv has no sheets, so it is parsed properly (through DataLoader, for the same
  encoding fallback) and reported as a single implicit sheet.

Row counts here must agree with what DataLoader/pandas will produce later, or the
upload screen promises a number the analysis then contradicts. That is why both
Excel paths scan cell values instead of reading the stored dimensions: Excel
writes the dimension record over the *used* range, which counts rows holding
nothing but formatting - leftovers from deleted data, which real spreadsheets are
full of. A sheet of 5 rows under a stray bold cell at row 20 reports 19, and a
formatting-only sheet reports non-empty and then loads as nothing.

Scanning costs a pass over the cells, which is the price of the two numbers
agreeing. read_only/on_demand keep that pass streaming, so memory stays flat.

Both readers count the header row, so it is subtracted; `empty` mirrors the
`df.empty` test that makes DataLoader skip a sheet entirely.
"""

import os

from app.data.data_loader import DataLoader, EXCEL_EXTENSIONS


def _extent(rows):
    """Last 1-based row index carrying a value, and the widest such row.

    Rows past that point exist in the file but hold only formatting, so they are
    not part of the table pandas will load. A cell is empty only when it holds
    nothing at all - None from openpyxl, '' from xlrd. Whitespace is a value to
    pandas, so '   ' has to count as data here or the two disagree again.
    """
    last_row = 0
    width = 0
    for i, row in enumerate(rows, start=1):
        filled = 0
        for j, value in enumerate(row, start=1):
            if value is not None and value != '':
                filled = j
        if filled:
            last_row = i
            width = max(width, filled)
    return last_row, width


def _probe_xlsx(path):
    """Returns worksheets in workbook order, which is the order the user sees in
    Excel."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return [
            _sheet_entry(ws.title, *_extent(ws.iter_rows(values_only=True)))
            for ws in wb.worksheets
        ]
    finally:
        # read_only holds the archive open until closed explicitly, which on
        # Windows blocks the temp dir from being removed.
        wb.close()


def _probe_xls(path):
    import xlrd

    book = xlrd.open_workbook(path, on_demand=True)
    try:
        sheets = []
        for name in book.sheet_names():
            # on_demand materialises the sheet here, so the scan reads memory
            # rather than the file.
            sheet = book.sheet_by_name(name)
            extent = _extent(sheet.row_values(r) for r in range(sheet.nrows))
            sheets.append(_sheet_entry(name, *extent))
            book.unload_sheet(name)
        return sheets
    finally:
        book.release_resources()


def _sheet_entry(name, raw_rows, columns):
    """Normalise one sheet. `raw_rows` counts the header, `rows` does not."""
    raw_rows = raw_rows or 0
    columns = columns or 0
    rows = max(raw_rows - 1, 0) if columns else 0
    return {
        "name": name,
        "rows": rows,
        "columns": columns,
        # A header-only or blank sheet produces an empty DataFrame, which
        # DataLoader._add_excel skips. Flagged so the UI can say so rather than
        # offer a checkbox that does nothing.
        "empty": rows == 0 or columns == 0,
    }


def inspect_file(path, name, size):
    """Describe one uploaded file. Never raises: a file that cannot be read comes
    back carrying an `error` so one bad workbook doesn't sink the whole batch."""
    ext = os.path.splitext(name)[1].lower()

    try:
        if ext == ".xlsx":
            sheets = _probe_xlsx(path)
            kind = "excel"
        elif ext == ".xls":
            sheets = _probe_xls(path)
            kind = "excel"
        else:
            df = DataLoader()._read_csv_with_encoding(path)
            sheets = []
            kind = "csv"
            return {
                "name": name, "size": size, "kind": kind, "sheets": sheets,
                "rows": len(df), "columns": len(df.columns),
            }
    except Exception as e:
        # Mirrors DataLoader._add_excel, which falls back to a CSV read when a
        # workbook won't parse - usually a CSV that was renamed to .xlsx.
        if ext in EXCEL_EXTENSIONS:
            try:
                df = DataLoader()._read_csv_with_encoding(path)
                return {
                    "name": name, "size": size, "kind": "csv", "sheets": [],
                    "rows": len(df), "columns": len(df.columns),
                }
            except Exception:
                pass
        print(f"[API] Could not inspect {name}: {type(e).__name__}: {e}")
        return {
            "name": name, "size": size, "kind": "unknown", "sheets": [],
            "rows": None, "columns": None,
            "error": f"Could not read this file ({type(e).__name__}).",
        }

    return {
        "name": name,
        "size": size,
        "kind": kind,
        "sheets": sheets,
        # Workbook total. Empty sheets contribute 0, so this already matches what
        # the analysis will load if every sheet stays selected.
        "rows": sum(s["rows"] for s in sheets),
    }
