import numpy as np
import pandas as pd
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

from data.data_loader import DataLoader
import app.data.data_info as explore

# Current Dataframe
df = None

# TODO: Implement importing filepath
file_path = "test_data_1.csv"

# Loading CSV File
test_load = DataLoader()
test_load.set_df(file_path)
df = test_load.get_df()

output = explore.get_columns(df)
print(output)

