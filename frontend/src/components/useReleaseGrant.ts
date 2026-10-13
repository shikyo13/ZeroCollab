import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage } from '../lib/api';
import { changeReleaseGrant, isStaleGrant, readReleaseGrant, type ReleaseGrant } from './releaseGrantApi';

/** The revision on screen is the only mutation target. Stale writes never retry. */
export function useReleaseGrant(project: string) {
  const [view, setView] = useState<ReleaseGrant | null>(null);
  const [viewProject, setViewProject] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const shown = useRef(view); shown.current = viewProject === project ? view : null;
  const activeProject = useRef(project); activeProject.current = project;
  const epoch = useRef(0), locked = useRef(false);
  const run = useCallback(async (enabled?: boolean) => {
    if (locked.current || !project) return;
    const inspected = shown.current;
    if (enabled !== undefined && !inspected) return;
    locked.current = true;
    const generation = epoch.current;
    const current = () => epoch.current === generation && activeProject.current === project;
    setBusy(true); setError(''); setNote('');
    try {
      if (enabled !== undefined) {
        await changeReleaseGrant(enabled, inspected!.revision);
        if (!current()) return;
        setView(null);
        setNote(enabled ? 'Permission enabled for Astra. Automatic deployments still wait for release checks and idle tabs.'
          : 'Permission revoked. Astra can no longer request automatic deployment.');
      }
      const next = await readReleaseGrant();
      if (current()) { setView(next); setViewProject(project); }
    } catch (failure) {
      if (!current()) return;
      setView(null); shown.current = null;
      if (isStaleGrant(failure)) {
        setError('Permission changed in another tab. Review the latest state and click again.');
        try {
          const next = await readReleaseGrant();
          if (current()) { setView(next); setViewProject(project); }
        } catch (reload) {
          if (current()) setError(`Permission changed. Refresh before trying again. ${errorMessage(reload)}`);
        }
      } else setError(errorMessage(failure));
    } finally {
      if (current()) { locked.current = false; setBusy(false); }
    }
  }, [project]);
  useEffect(() => {
    shown.current = null; setView(null); setNote(''); setError('');
    void run();
    return () => { ++epoch.current; locked.current = false; };
  }, [run]);
  return { view: viewProject === project ? view : null, busy, error, note, run };
}
