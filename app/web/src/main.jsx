import { createRoot } from 'react-dom/client';
import App from './App';

// The React app previously shipped with no stylesheet at all - tokens.css existed but
// was only imported by the dead vanilla-JS entry point, so every `className` in the
// components matched nothing.
import './css/tokens.css';
import './css/dashboard.css';

createRoot(document.getElementById('root')).render(<App />);
