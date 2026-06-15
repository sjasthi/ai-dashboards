from data_loader import (
    load_and_summarize
)

from ai_engine import (
    get_report_recommendations
)

print("Loading CSV files...")

files = [
    "sample_data/file1.csv",
    "sample_data/file2.csv"
]

summary = load_and_summarize(files)

print("\nDATA SUMMARY")
print("=" * 50)

print(summary)

print("\nSending data to Claude...")

recommendations = (
    get_report_recommendations(summary)
)

print("\nREPORT RECOMMENDATIONS")
print("=" * 50)

print(recommendations)
