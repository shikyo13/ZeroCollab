import { createRoot } from 'react-dom/client';
import App from './App';
import { ErrorBoundary } from './components/ErrorBoundary';
import { prepareLegacy } from './legacy';
import { brand } from './components/brand';
import './styles.css';

prepareLegacy();
const icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
if (icon) icon.href = brand.favicon;
const root = document.createElement('div');
root.id = 'workspace-root';
document.body.append(root);
document.body.classList.add('modern-workspace');
createRoot(root).render(<ErrorBoundary><App /></ErrorBoundary>);
