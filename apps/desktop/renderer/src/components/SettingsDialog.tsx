/* Settings as a dialog. The fields are settings.js's own #settings-bar, moved here rather
 * than re-implemented, so auto-save (blur/Enter for text, change for selects) and the sound
 * rows keep working. window.toggleSettings opens and closes it, so the sidebar button and
 * the fields' Escape handlers need no changes. */
import { useEffect, useRef } from 'react';
import { legacy } from '../legacy';
import { LegacySlot } from './LegacySlot';

export function SettingsDialog() {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = window.toggleSettings;
    window.toggleSettings = () => {
      const node = dialog.current;
      if (!node) return;
      if (node.open) { node.close(); return; }
      node.showModal();
      document.getElementById('setting-username')?.focus();
    };
    return () => { window.toggleSettings = previous; };
  }, []);
  // Closing blurs the focused field, which is what saves a text field.
  const close = () => dialog.current?.close();
  return <dialog ref={dialog} className="workspace-help workspace-settings" aria-labelledby="workspace-settings-title">
    <div className="workspace-page-heading">
      <div><h2 id="workspace-settings-title">Settings</h2><p>Changes save automatically.</p></div>
      <button onClick={close} aria-label="Close settings">✕</button>
    </div>
    <LegacySlot node={legacy.settings} className="workspace-settings-body" />
    <div className="workspace-help-footer"><span /><button className="workspace-primary" onClick={close}>Done</button></div>
  </dialog>;
}
