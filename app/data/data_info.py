import numpy as np
import pandas as pd

def get_head(df, rows=5):
    return df.head(rows)

def get_tail(df, rows=5):
    return df.tail(rows)

def get_stats(df):
    return df.describe()

def get_columns(df):
    columns = np.array(df.columns, dtype=str)
    return columns
