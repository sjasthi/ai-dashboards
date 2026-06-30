import pandas as pd

class DataLoader:
    
    def __init__(self):
        self.files = []

    def add_files(self, file_paths):
        """Add files, handling encoding issues automatically."""
        for file_path in file_paths:
            df = self._read_csv_with_encoding(file_path)
            self.files.append((file_path, df))

    def _read_csv_with_encoding(self, file_path):
        """Try to read CSV with multiple encoding options."""
        encodings = ['utf-8', 'utf-16', 'latin-1', 'iso-8859-1', 'cp1252']
        
        for encoding in encodings:
            try:
                df = pd.read_csv(file_path, encoding=encoding)
                print(f"✓ Loaded {file_path} (encoding: {encoding})")
                return df
            except (UnicodeDecodeError, UnicodeError):
                continue
        
        # If all fail, raise informative error
        raise ValueError(
            f"Could not read {file_path} with any supported encoding. "
            f"Tried: {encodings}"
        )

    def clear(self):
        self.files = []


