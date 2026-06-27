import pandas as pd

class DataLoader:
    
    def __init__(self):
        self.files = []

    def add_files(self, file_paths):
        for file_path in file_paths:
            df = pd.read_csv(file_path)
            self.files.append((file_path, df))

    def clear(self):
        self.files = []


