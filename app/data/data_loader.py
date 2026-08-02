import os
import pandas as pd

EXCEL_EXTENSIONS = {".xlsx", ".xls"}

class DataLoader:

    def __init__(self):
        self.files = []
        # Table name -> the uploaded file it came from. "Orders (sales).xlsx" can't
        # be parsed back reliably, since sheet names and workbook stems may both
        # contain parentheses, so the mapping is recorded while it is still known.
        self.origins = {}
        # Uploaded filename -> {"kind", "sheet_count"}: what the file turned out to
        # be once opened. Keyed per upload rather than per table, so a workbook
        # whose sheets were all deselected or empty still reports how many it holds.
        self.sources = {}

    def add_files(self, file_paths, selections=None):
        """Add files, dispatching by extension. CSVs are read with encoding
        detection; Excel workbooks contribute one entry per worksheet.

        selections optionally narrows which worksheets are loaded, as
        {uploaded filename: [sheet names]}. A workbook absent from the mapping
        contributes every sheet, so callers that don't care can omit it entirely.
        Filtering here means excluded sheets never reach profiling, the prompt or
        the tables map.
        """
        for file_path in file_paths:
            ext = os.path.splitext(file_path)[1].lower()
            if ext in EXCEL_EXTENSIONS:
                self._add_excel(file_path, selections)
            else:
                df = self._read_csv_with_encoding(file_path)
                self._note(file_path, "csv")
                self._record(file_path, df, os.path.basename(file_path))

    def _record(self, name, df, source):
        self.files.append((name, df))
        self.origins[os.path.basename(name)] = source

    def _note(self, file_path, kind, sheet_count=None):
        """What one uploaded file turned out to be. sheet_count stays None for a
        CSV: it has no worksheets at all, which is a different fact from a
        one-sheet workbook."""
        self.sources[os.path.basename(file_path)] = {
            "kind": kind, "sheet_count": sheet_count,
        }

    def _add_excel(self, file_path, selections=None):
        try:
            sheets = pd.read_excel(file_path, sheet_name=None)
        except ValueError:
            # Handle CSV mislabeled as xlsx.
            df = self._read_csv_with_encoding(file_path)
            self._note(file_path, "csv")
            self._record(file_path, df, os.path.basename(file_path))
            return
        base = os.path.basename(file_path)
        stem, ext = os.path.splitext(base)

        # Before any filtering: this is the workbook's own size, not how much of
        # it was asked for. read_excel(sheet_name=None) has already read them all.
        self._note(file_path, "excel", len(sheets))

        chosen = (selections or {}).get(base)

        # Collect survivors before naming anything: both the selection filter and
        # the empty-sheet skip can drop the count below two, and the parenthesised
        # name is only warranted when a sheet really does have siblings.
        kept = []
        for sheet_name, df in sheets.items():
            if chosen is not None and sheet_name not in chosen:
                continue
            if df.empty:
                continue
            # Strip only string headers; Excel headers can be numeric/blank
            df.columns = df.columns.map(lambda c: c.strip() if isinstance(c, str) else c)
            kept.append((sheet_name, df))

        multi = len(kept) > 1
        for sheet_name, df in kept:
            # Lead with the sheet name: for a multi-sheet workbook the sheet is the
            # meaningful table identity, and relationship detection matches foreign
            # keys against the *start* of the file stem (see summary_builder). The
            # workbook stem in parens keeps names unique across workbooks.
            display = f"{sheet_name} ({stem}){ext}" if multi else base
            self._record(display, df, base)
            print(f"Loaded {file_path} (sheet: {sheet_name})")

    def _read_csv_with_encoding(self, file_path):
        """Try to read CSV with multiple encoding options."""
        encodings = ['utf-8', 'utf-16', 'latin-1', 'iso-8859-1', 'cp1252']
        
        for encoding in encodings:
            try:
                df = pd.read_csv(file_path, encoding=encoding)
                df.columns = df.columns.str.strip()
                print(f"Loaded {file_path} (encoding: {encoding})")
                return df
            except (UnicodeDecodeError, UnicodeError):
                continue
        
        # If all fail, raise informative error
        raise ValueError(
            f"Could not read {file_path} with any supported encoding. "
            f"Tried: {encodings}"
        )

    def tables(self):
        """Map table name -> DataFrame, keyed the same way profiling names files
        for the LLM. Worksheets aren't files on disk, so this is the only way to
        get a sheet's data back after loading."""
        return {os.path.basename(name): df for name, df in self.files}


