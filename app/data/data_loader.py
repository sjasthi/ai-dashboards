import os
import pandas as pd

EXCEL_EXTENSIONS = {".xlsx", ".xls"}

class DataLoader:

    def __init__(self):
        self.files = []

    def add_files(self, file_paths):
        """Add files, dispatching by extension. CSVs are read with encoding
        detection; Excel workbooks contribute one entry per worksheet."""
        for file_path in file_paths:
            ext = os.path.splitext(file_path)[1].lower()
            if ext in EXCEL_EXTENSIONS:
                self._add_excel(file_path)
            else:
                df = self._read_csv_with_encoding(file_path)
                self.files.append((file_path, df))

    def _add_excel(self, file_path):
        try:
            sheets = pd.read_excel(file_path, sheet_name=None) 
        except ValueError:
            # Handle CSV mislabeled as xlsx.
            df = self._read_csv_with_encoding(file_path)
            self.files.append((file_path, df))
            return
        base = os.path.basename(file_path)
        stem, ext = os.path.splitext(base)
        multi = len(sheets) > 1
        for sheet_name, df in sheets.items():
            if df.empty:
                continue
            # Strip only string headers; Excel headers can be numeric/blank
            df.columns = df.columns.map(lambda c: c.strip() if isinstance(c, str) else c)
            # Lead with the sheet name: for a multi-sheet workbook the sheet is the
            # meaningful table identity, and relationship detection matches foreign
            # keys against the *start* of the file stem (see summary_builder). The
            # workbook stem in parens keeps names unique across workbooks.
            display = f"{sheet_name} ({stem}){ext}" if multi else base
            self.files.append((display, df))
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

    def clear(self):
        self.files = []


