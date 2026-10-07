import { useEffect, useRef } from 'react';
import { brand } from './brand';

export function Help({ open, onClose }: { open: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal();
    else if (!open) dialog.current?.close();
  }, [open]);
  return <dialog ref={dialog} className="workspace-help" onCancel={onClose} onClose={onClose} aria-labelledby="workspace-help-title">
    <div className="workspace-page-heading"><div><span className="workspace-eyebrow">A QUICK ORIENTATION</span><h2 id="workspace-help-title">Your room, at a glance</h2></div><button onClick={onClose} aria-label="Close help">✕</button></div>
    <p>Start with a conversation. Add structure whenever your work needs it.</p>
    <div className="workspace-help-grid">
      <section><h3>Pick a project</h3><p>Each project keeps its own board, team, goal and agent worktrees. Use the project picker or Projects to see them all and switch. Choose New project to start without a repository; add one later if needed.</p></section>
      <section><h3>Chat together</h3><p>Chat tabs organize conversations within the active project. Use + New chat to start another. Mention an agent with @name, or use Address team. Drop or paste images into the composer.</p></section>
      <section><h3>Keep work visible</h3><p>The Board tracks owners, reserved files, dependencies, check-ins, reviews and follow-ups. A project spec is optional.</p></section>
      <section><h3>Build your team</h3><p>Open Team to create named agents with their own prompt, color, model and reasoning effort. Archive agents without losing their history.</p></section>
      <section><h3>Stay in control</h3><p>Stop all is always available. Team contains individual stop controls and model settings; Activity shows approvals and tool calls. Send a new chat message to steer ongoing work.</p></section>
    </div>
    <details><summary>More tools & shortcuts</summary><p>Hover a chat message and choose create task to put it on the Board. Configure agent expertise in Team. Use /goal to set a shared outcome; goal pause and cancellation controls stay above every view. Settings holds your name, font, contrast, history and sounds. The composer supports voice input and the /goal and /clear commands. Enter sends; Shift+Enter adds a line. Open a chat menu to rename it.</p></details>
    <div className="workspace-help-footer"><button className="workspace-primary" onClick={onClose}>Got it</button><span>{brand.name} · {brand.attribution}</span></div>
  </dialog>;
}
