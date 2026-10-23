import { useLayoutEffect, useState } from 'react';
import { useRoomEvent } from '../lib/room';

/** Authentication rejection is terminal for this tab; keep drafts and reading state. */
export function SessionNotice() {
  const [signedOut, setSignedOut] = useState(() =>
    (window as Window & { roomConnectionState?: string }).roomConnectionState === 'signed_out');
  // The legacy client can reject the socket before React mounts. Its fallback
  // belongs only to legacy rendering; the workspace owns the notice from here.
  useLayoutEffect(() => { document.getElementById('room-session-notice')?.remove(); }, []);
  useRoomEvent('chat_connection', event => {
    if ((event as unknown as { state?: string }).state === 'signed_out') setSignedOut(true);
  });
  return signedOut ? <div className="deploy-notice" role="alert">
    This browser isn't signed in. Open ZeroCollab from the tray or Start-Room.
  </div> : null;
}
