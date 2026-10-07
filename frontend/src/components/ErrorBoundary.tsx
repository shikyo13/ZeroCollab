import { Component, type ErrorInfo, type ReactNode } from 'react';
import { legacy } from '../legacy';

interface State {
  error: Error | null;
}

/** Keeps a render error in one view from blanking the whole workspace. The chat and
 * runtime panels are live legacy DOM moved into React slots; if the shell unmounts
 * they are put back on the page so chat keeps working while the error is shown. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Workspace render failed', error, info.componentStack);
    for (const node of [legacy.chat, legacy.runtime]) {
      if (!node.isConnected) document.body.append(node);
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return <div className="workspace-crash" role="alert">
      <p>The workspace hit an error and switched to the basic chat view: {error.message}</p>
      <button onClick={() => window.location.reload()}>Reload</button>
    </div>;
  }
}
