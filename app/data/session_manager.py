from pathlib import Path
from datetime import datetime
from dataclasses import asdict
from typing import List
import json

class SessionManager:
    """Centralized session logging and file persistence."""
    # TODO: FIX SAVE LOCATIONS 
    def __init__(self, logs_dir="session_data/logs", profiles_dir="session_Data/profiles"):
        self.logs_dir = Path(logs_dir)
        self.profiles_dir = Path(profiles_dir)
        self.session_id = self._create_session()
    
    def _create_session(self) -> str:
        """Create timestamped session directory."""
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = self.logs_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_id
    
    def save_profiles(self, profiles, filename="profiles.json") -> None:
        """Save file profiles to disk."""
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        
        for profile in profiles:
            output_file = self.profiles_dir / f"{profile.filename.replace('.csv', '')}_profile.json"
            with open(output_file, 'w') as f:
                json.dump(asdict(profile), f, indent=2, default=str)
            print(f"✓ Profile saved: {output_file}")
    
    def save_prompt(self, prompt: str, filename="prompt_request.txt") -> None:
        """Save LLM prompt request to session logs."""
        log_file = self.logs_dir / self.session_id / filename
        with open(log_file, 'w') as f:
            f.write(prompt)
        print(f"✓ Prompt saved: {log_file}")
    
    def save_response(self, response: str, filename="response.txt") -> None:
        """Save LLM response to session logs."""
        log_file = self.logs_dir / self.session_id / filename
        with open(log_file, 'w') as f:
            f.write(response)
        print(f"✓ Response saved: {log_file}")
    
    def get_session_dir(self) -> Path:
        """Get current session directory path."""
        return self.logs_dir / self.session_id