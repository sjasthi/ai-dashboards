import tkinter as tk
from tkinter import filedialog

from data.data_loader import DataLoader
import data.prompt_builder as builder
import data.AI_Engine as ai

def select_files():
    root = tk.Tk()
    root.withdraw()

    file_paths = filedialog.askopenfilenames(
        title="Select CSV Files",
        filetypes=[("CSV Files", "*.csv")],
        initialdir="datasets"
    )

    return list(file_paths)

# Loads each file from list
loader = DataLoader()
loader.add_files(select_files())

if not loader.files:
    print("No files selected. Exiting.")
    exit()

# List uploaded files
print(f"\nFiles Uploaded \n ------")
for filename, df in loader.files:
    print(f"{filename}")

# Prompt Builder:
prompt = builder.build_prompt(loader, report_goal="<TBD>")

# Interact with AI Agent:
response = ai.send_prompt(prompt)
print(response)
