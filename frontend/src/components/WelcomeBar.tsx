/* One-line orientation shown until the user dismisses it (remembered in localStorage). */
import { useState } from 'react';
import { STORAGE_KEYS } from '../lib/constants';
import { readStored, writeStored } from '../lib/storage';

export function WelcomeBar({ onHelp }: { onHelp: () => void }) {
  const [shown, setShown] = useState(() => readStored(STORAGE_KEYS.welcomeDismissed) !== '1');
  if (!shown) return null;
  const dismiss = () => {
    setShown(false);
    writeStored(STORAGE_KEYS.welcomeDismissed, '1');
  };
  return (
    <div className="workspace-welcome">
      <span>Start in Chat. Track work on the Board. Manage agents in Team.</span>
      <button onClick={onHelp}>Quick guide</button>
      <button onClick={dismiss} aria-label="Dismiss welcome">✕</button>
    </div>
  );
}
