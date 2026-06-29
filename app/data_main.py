import tkinter as tk
from tkinter import filedialog

from data.data_loader import DataLoader
from data.summary_builder import SummaryGenerator
from data.recommendation_requester import RecommendationRequester
from data.session_manager import SessionManager
import data.AI_Engine as ai
import json

def select_files():
    root = tk.Tk()
    root.withdraw()

    file_paths = filedialog.askopenfilenames(
        title="Select CSV Files",
        filetypes=[("CSV Files", "*.csv")],
        initialdir="datasets"
    )

    return list(file_paths)

# Load Files
loader = DataLoader()
loader.add_files(select_files())

# Generate summaries
summary_gen = SummaryGenerator()
file_profiles = summary_gen.profile_all_files(loader)

# Build recommendation prompt for LLM
requester = RecommendationRequester()
prompt = requester.build_request_prompt(file_profiles)

print("\n" + "="*60)
print("SENDING PROMPT TO AI AGENT")
print("="*60 + "\n")

# Get response from LLM
response = ai.send_prompt(prompt)

# Validate and parse response
print("\n" + "="*60)
print("RESPONSE FROM AI AGENT")
print("="*60 + "\n")
print(response)

# Try validating JSON structure
try:
    response_data = json.loads(response)
    print("\n✓ Response is valid JSON")
    print(f"✓ Found {len(response_data.get('recommendations', []))} recommendations")
    
except json.JSONDecodeError as e:
    print(f"\n✗ Response is not valid JSON: {e}")
    print("Raw response:", response)

### Save files for review ###
session = SessionManager()

session.save_profiles(file_profiles)
session.save_prompt(prompt)
session.save_response(response)