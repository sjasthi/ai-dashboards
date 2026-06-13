import pandas as pd

class DataLoader:
    
    def __init__(self):
        self.files = []

    # TODO: Add handling for naming conflicts
    def add_file(self, file_path):
        df = pd.read_csv(file_path)
        self.files.append((file_path, df))




