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
      <section><h3>Projects and chats</h3><p>Each project keeps its own chats, board, team and goal. The sidebar lists the active project's chats under its name; select another project or All projects to switch. New project starts without a repository, and you can add one later.</p></section>
      <section><h3>Chat together</h3><p>New chat at the top of the sidebar starts a conversation in the active project. Mention an agent with @handle, or select @all above the message box to reach everyone. Paste or drop images into the composer.</p></section>
      <section><h3>Keep work visible</h3><p>The Board tracks owners, reserved files, dependencies, check-ins, reviews and follow-ups. Hover a chat message and choose Create task to put it on the Board.</p></section>
      <section><h3>Build your team</h3><p>Team is where you create agents and set each one's handle, prompt, color, model and reasoning effort. Archive an agent without losing its history.</p></section>
      <section><h3>Stay in control</h3><p>The header shows who is working, with Stop all and Resume beside it. Send a chat message to steer work in progress. Activity holds agent requests, tool calls and deployments, and Server at the bottom left starts or restarts the room.</p></section>
    </div>
    <details><summary>More tools & shortcuts</summary><p>Ctrl K searches chats and projects. The panel button at the top right opens the shared browser and file viewer. Type /goal to set a shared outcome; while a goal runs, its chip in the header shows progress and controls. /clear clears the current chat. Enter sends and Shift+Enter adds a line. Hover a chat in the sidebar to rename or close it. Settings holds your name, font, contrast, sounds and history.</p></details>
    <div className="workspace-help-footer"><button className="workspace-primary" onClick={onClose}>Got it</button><span>{brand.name} · {brand.attribution}</span></div>
  </dialog>;
}
