import { createRoot } from 'react-dom/client';
import App from './App';

// Both stylesheets must be imported here, or every component className matches nothing.
import './css/tokens.css';
import './css/dashboard.css';

createRoot(document.getElementById('root')).render(<App />);
