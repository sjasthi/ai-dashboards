import pandas as pd

class DataLoader:
    
    def __init__(self):
        self.df = None

    def set_df(self, file_path):
        self.df = pd.read_csv(file_path)

    def get_df(self):
        if self.df is None:
            raise ValueError("No file loaded")
        return self.df



