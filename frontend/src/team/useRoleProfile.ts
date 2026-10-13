import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage } from '../lib/api';
import { changeRole, readRole, staleRole, type RoleAction, type RoleResponse } from './roleProfileApi';

type RequestKind = RoleAction | 'refresh';

/** One selected profile, one request at a time. No polling or automatic approval. */
export function useRoleProfile(name: string) {
  const [view, setView] = useState<RoleResponse | null>(null);
  const [busy, setBusy] = useState<RequestKind | null>(null);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const shown = useRef(view);
  shown.current = view;
  const generation = useRef(0);
  const locked = useRef(false);

  const run = useCallback(async (kind: RequestKind) => {
    if (locked.current) return;
    const digest = shown.current?.draft?.digest;
    if (kind === 'approve' && !digest) return;
    locked.current = true;
    const epoch = generation.current;
    const alive = () => epoch === generation.current;
    setBusy(kind); setError('');
    if (kind !== 'refresh') setNote('');
    try {
      if (kind !== 'refresh') {
        const current = await changeRole(name, kind, digest);
        if (!alive()) return;
        setView(previous => previous ? { ...previous, current } : null);
        setNote(current.note);
      }
      const next = await readRole(name);
      if (alive()) setView(next);
    } catch (failure) {
      if (!alive()) return;
      if (staleRole(failure)) {
        // Remove the old approval target immediately. A failed reload must not
        // leave its digest available for another approval attempt.
        setView(previous => previous ? { ...previous, draft: null } : null);
        setError('The draft changed since you reviewed it. Review the refreshed core and skill versions, then approve again.');
        try {
          const next = await readRole(name);
          if (alive()) setView(next);
        } catch (reloadFailure) {
          if (alive()) setError(`The draft changed. Refresh it before approving. ${errorMessage(reloadFailure)}`);
        }
      } else setError(errorMessage(failure));
    } finally {
      if (alive()) { locked.current = false; setBusy(null); }
    }
  }, [name]);

  useEffect(() => {
    setView(null); setNote(''); setError('');
    void run('refresh');
    return () => { ++generation.current; locked.current = false; };
  }, [run]);

  return { view, busy, error, note, run };
}
