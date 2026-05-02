import React from "react";
import { AlertCircle } from "lucide-react";

interface Props {
  children: React.ReactNode;
  label?: string;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary]", this.props.label || "", error, info);
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-fg-muted gap-2 p-6">
        <AlertCircle size={28} className="text-danger" />
        <span className="text-sm text-danger">渲染崩溃</span>
        {this.props.label && (
          <span className="text-xs text-fg-subtle">位置: {this.props.label}</span>
        )}
        <span className="text-xs text-center break-all max-w-xl">{error.message}</span>
        {error.stack && (
          <pre className="text-xs text-fg-subtle bg-bg-sidebar p-2 rounded max-h-40 overflow-auto whitespace-pre-wrap break-all max-w-2xl">
            {error.stack.split("\n").slice(0, 8).join("\n")}
          </pre>
        )}
        <button onClick={this.reset} className="btn">重试</button>
      </div>
    );
  }
}
