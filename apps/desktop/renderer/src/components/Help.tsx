import { useEffect, useRef } from 'react';
import { brand } from './brand';

export function Help({ open, onClose }: { open: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal();
    else if (!open) dialog.current?.close();
  }, [open]);
  return <dialog ref={dialog} className="workspace-help" onCancel={onClose} onClose={onClose} aria-labelledby="workspace-help-title">
    <div className="workspace-page-heading"><div><span className="workspace-eyebrow">GUIDE</span><h2 id="workspace-help-title">Your AI team, in one room</h2></div><button onClick={onClose} aria-label="Close guide">✕</button></div>

    <section className="workspace-help-section">
      <h3>Get started</h3>
      <ol className="workspace-help-steps">
        <li><b>Connect a model.</b> Open Team, then Providers. Sign in to Claude, Codex or Gemini with their own apps; {brand.name} never sees your password. To run models on this PC instead, start Ollama, LM Studio or llama.cpp and it appears under Local models.</li>
        <li><b>Add agents.</b> In Team, choose Add agent. Or choose Build a team, describe your project, and approve the team your connected agents propose. Give each agent a name, a provider and model, and a short description of its job.</li>
        <li><b>Talk to them.</b> Choose New chat and write what you want. Mention one agent with @handle, or @all to reach the whole team. They split the work, put it on the Board and report back in the chat.</li>
      </ol>
      <img className="workspace-help-shot" src="/static/guide/providers.jpg" alt="Team, Providers tab: Codex, Claude and Gemini signed in, and Ollama running under Local models" loading="lazy" />
    </section>

    <section className="workspace-help-section">
      <h3>What you can do</h3>
      <div className="workspace-help-grid">
        <div><h4>Board</h4><p>Every task, who owns it and what it is waiting on. Click a task for its next step and history.</p></div>
        <div><h4>Live</h4><p>Watch the team work: who is talking to whom and what each agent is looking at. Anything that needs your answer appears in the You card.</p></div>
        <div><h4>Local models</h4><p>Run agents on models on your own PC through Ollama, LM Studio or llama.cpp, next to cloud agents in the same team.</p></div>
        <div><h4>Desktop app</h4><p>{brand.name} runs as an app on your PC and reopens your last room. When an update is ready, it installs it and reopens the room for you.</p></div>
        <div><h4>Videos and recordings</h4><p>Agents can record the shared browser and make short videos. They play right in the chat.</p></div>
        <div><h4>Shared browser</h4><p>Open the side panel at the top right to see the browser the agents use. Take control to click around yourself, then hand it back.</p></div>
      </div>
      <img className="workspace-help-shot" src="/static/guide/live.jpg" alt="Live: the team as people on a stage, with what each one is doing and the pictures they are working on" loading="lazy" />
    </section>

    <section className="workspace-help-section">
      <h3>Stay in control</h3>
      <p>Stop all in the header stops every agent; Resume starts them again. Write in the chat at any time to steer work in progress. The bell shows requests waiting on you.</p>
    </section>

    <details><summary>Shortcuts</summary><p>Ctrl K searches chats, projects and views. Ctrl Shift B shows or hides the side panel. Enter sends and Shift+Enter adds a line. Paste or drop images into the message box. Type /goal to set a shared outcome, and /clear to clear the current chat. Settings holds your name, font, contrast and sounds.</p></details>
    <div className="workspace-help-footer"><button className="workspace-primary" onClick={onClose}>Got it</button><span>{brand.name} · {brand.attribution}</span></div>
  </dialog>;
}
