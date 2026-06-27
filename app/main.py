import app.data.prompt_builder as prompt
from data.data_loader import DataLoader
import data.AI_Engine

print("Loading CSV files...")

files = [
    "datasets/courses.csv",
    "datasets/grades.csv",
    "datasets/students.csv",
]

loader = DataLoader()
loader.add_files(files)

summary = prompt.build_prompt(loader, report_goal="<TBD>")

print("\nDATA SUMMARY")
print("=" * 50)

print(summary)

print("\nSending data to Claude...")

recommendations = data.AI_Engine.get_report_recommendations(summary)

print("\nREPORT RECOMMENDATIONS")
print("=" * 50)

print(recommendations)
