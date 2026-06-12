import numpy as np
import pandas as pd
import app.data.data_loader as data_loader
import kagglehub

path = kagglehub.dataset_download("ricgomes/global-fashion-retail-stores-dataset")

print("Path to dataset files:", path)
