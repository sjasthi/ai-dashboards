from pathlib import Path
from datetime import datetime

class SessionManager:
    """Centralized session logging and file persistence."""
    
    def __init__(self, logs_dir="session_data", session_id=None):
        self.logs_dir = Path(logs_dir)
        self.session_id = self._use_existing_session(session_id) if session_id else self._create_session()

    def _use_existing_session(self, session_id: str) -> str:
        """Reuse an already-existing session directory (e.g. the API's upload session_id)."""
        session_dir = self.logs_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_id

    def _create_session(self) -> str:
        """Create timestamped session directory."""
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = self.logs_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_id
    
    def save_response(self, response: str, filename="prompt_response.txt") -> None:
        """Save LLM response to session directory."""
        log_file = self.logs_dir / self.session_id / filename
        with open(log_file, 'w') as f:
            f.write(response)
        print(f"Response saved: {log_file}")
    
    def get_session_dir(self) -> Path:
        """Get current session directory path."""
        return self.logs_dir / self.session_id