import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { Actor, getRuntimeColor, RUNTIME_INFO } from "../types";
import { getTerminalTheme } from "../hooks/useTheme";

function classNames(...xs: Array<string | false | null | undefined>) {
  return xs.filter(Boolean).join(" ");
}

interface AgentTabProps {
  actor: Actor;
  groupId: string;
  isVisible: boolean;
  onQuit: () => void;
  onLaunch: () => void;
  onRelaunch: () => void;
  onEdit: () => void;
  onRemove: () => void;
  onInbox: () => void;
  busy: string;
  isDark: boolean;
}

export function AgentTab({
  actor,
  groupId,
  isVisible,
  onQuit,
  onLaunch,
  onRelaunch,
  onEdit,
  onRemove,
  onInbox,
  busy,
  isDark,
}: AgentTabProps) {
  const termRef = useRef<HTMLDivElement>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);

  const isRunning = actor.running ?? actor.enabled ?? false;
  const isHeadless = actor.runner === "headless";
  const color = getRuntimeColor(actor.runtime, isDark);
  const rtInfo = (actor.runtime && RUNTIME_INFO[actor.runtime]) ? RUNTIME_INFO[actor.runtime] : RUNTIME_INFO.codex;
  const unreadCount = actor.unread_count ?? 0;

  // Send interrupt (Ctrl+C)
  const sendInterrupt = () => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ t: "i", d: "\x03" }));
  };

  // Update terminal theme when isDark changes
  useEffect(() => {
    if (terminalRef.current) {
      terminalRef.current.options.theme = getTerminalTheme(isDark);
    }
  }, [isDark]);

  // Initialize terminal
  useEffect(() => {
    if (!termRef.current || isHeadless || !isRunning) return;

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: '"JetBrains Mono", "Fira Code", "SF Mono", Menlo, Monaco, monospace',
      theme: getTerminalTheme(isDark),
      allowProposedApi: true,
    });

    const fitAddon = new FitAddon();
    
    term.loadAddon(fitAddon);
    term.open(termRef.current);
    
    terminalRef.current = term;
    fitAddonRef.current = fitAddon;

    // Initial fit
    setTimeout(() => fitAddon.fit(), 0);

    return () => {
      term.dispose();
      terminalRef.current = null;
      fitAddonRef.current = null;
    };
  }, [isHeadless, isRunning]);

  // Connect WebSocket when visible and running
  useEffect(() => {
    if (!isVisible || !isRunning || isHeadless || !terminalRef.current) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/api/v1/groups/${encodeURIComponent(groupId)}/actors/${encodeURIComponent(actor.id)}/term`;
    
    const ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";
    
    ws.onopen = () => {
      setConnected(true);
      // Send initial resize
      const term = terminalRef.current;
      if (term) {
        ws.send(JSON.stringify({ t: "r", c: term.cols, r: term.rows }));
      }
    };
    
    ws.onmessage = (event) => {
      if (terminalRef.current) {
        if (event.data instanceof ArrayBuffer) {
          const data = new TextDecoder().decode(event.data);
          terminalRef.current.write(data);
        } else if (typeof event.data === "string") {
          // Server might send JSON messages for status
          try {
            const msg = JSON.parse(event.data);
            if (msg.ok === false && msg.error) {
              terminalRef.current.write(`\r\n[error] ${msg.error.message || "Unknown error"}\r\n`);
            }
          } catch {
            // Not JSON, write as text
            terminalRef.current.write(event.data);
          }
        }
      }
    };
    
    ws.onclose = () => {
      setConnected(false);
    };
    
    ws.onerror = () => {
      setConnected(false);
    };

    wsRef.current = ws;

    // Handle terminal input - send as JSON with type "i" (input)
    const term = terminalRef.current;
    const disposable = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        // xterm.js may emit terminal replies (not user keystrokes), e.g. device attributes.
        // Some runtimes (notably droid) can echo these back as literal text (seen as "1;2c").
        if (actor.runtime === "droid") {
          const isDeviceAttributesReply = /^\x1b\[(?:\?|>)(?:\d+)(?:;\d+)*c$/.test(data);
          if (isDeviceAttributesReply) return;
        }
        ws.send(JSON.stringify({ t: "i", d: data }));
      }
    });

    // Handle terminal resize - send as JSON with type "r" (resize)
    const resizeDisposable = term.onResize(({ cols, rows }) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ t: "r", c: cols, r: rows }));
      }
    });

    return () => {
      disposable.dispose();
      resizeDisposable.dispose();
      ws.close();
      wsRef.current = null;
      setConnected(false);
    };
  }, [isVisible, isRunning, isHeadless, groupId, actor.id]);

  // Fit terminal on visibility change and resize
  useEffect(() => {
    if (!isVisible || !fitAddonRef.current) return;
    
    const fit = () => {
      if (fitAddonRef.current) {
        fitAddonRef.current.fit();
      }
    };

    // Fit when becoming visible
    setTimeout(fit, 50);

    // Fit on window resize
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [isVisible]);

  const isBusy = busy.includes(actor.id);

  return (
    <div className="flex flex-col h-full">
      {/* Agent Header */}
      <div className={classNames(
        "flex items-center justify-between px-4 py-3 border-b",
        color.border, color.bg
      )}>
        <div className="flex items-center gap-3">
          <span className={classNames("w-3 h-3 rounded-full", isRunning ? "bg-emerald-500" : isDark ? "bg-slate-600" : "bg-gray-400")} />
          <div>
            <div className="flex items-center gap-2">
              <span className={classNames("font-semibold", color.text)}>{actor.id}</span>
              {actor.role === "foreman" && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-300 font-medium">
                  foreman
                </span>
              )}
            </div>
            <div className="text-xs text-slate-400">
              {rtInfo?.label || "Custom"} • {isRunning ? "Running" : "Stopped"}
              {isHeadless && " • Headless"}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-400">
          {connected && <span className="text-emerald-400">● Connected</span>}
        </div>
      </div>

      {/* Terminal or Status Area */}
      <div className={classNames("flex-1 min-h-0", isDark ? "bg-slate-950" : "bg-gray-50")}>
        {isHeadless ? (
          // Headless agent - show status
          <div className="flex flex-col items-center justify-center h-full text-slate-400 dark:text-slate-400 p-8">
            <div className="text-4xl mb-4">🤖</div>
            <div className="text-lg font-medium mb-2">Headless Agent</div>
            <div className="text-sm text-center max-w-md">
              This agent runs without a terminal. It communicates via MCP tools and the inbox system.
            </div>
            {isRunning && (
              <div className="mt-4 px-3 py-1.5 rounded bg-emerald-900/30 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-300 text-sm">
                Status: Running
              </div>
            )}
          </div>
        ) : isRunning ? (
          // PTY agent - show terminal
          <div ref={termRef} className="h-full w-full" />
        ) : (
          // Stopped agent
          <div className="flex flex-col items-center justify-center h-full text-slate-500 dark:text-slate-400 p-8">
            <div className="text-4xl mb-4">⏹</div>
            <div className="text-lg font-medium mb-2">Agent Not Running</div>
            <div className="text-sm text-center max-w-md mb-4">
              Click Launch to start this agent's terminal session.
            </div>
            <button
              onClick={onLaunch}
              disabled={isBusy}
              className="px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium disabled:opacity-50 min-h-[44px] transition-colors"
              aria-label="Launch agent"
            >
              {isBusy ? "Launching..." : "▶ Launch Agent"}
            </button>
          </div>
        )}
      </div>

      {/* Action Buttons */}
      <div className={classNames(
        "flex items-center gap-2 px-4 py-3 border-t",
        isDark ? "bg-slate-900/50 border-slate-800" : "bg-gray-100 border-gray-200"
      )}>
        {isRunning ? (
          <>
            <button
              onClick={onQuit}
              disabled={isBusy}
              className={classNames(
                "flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm disabled:opacity-50 min-h-[44px] transition-colors",
                isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-white hover:bg-gray-50 text-gray-700 border border-gray-300"
              )}
              aria-label="Quit agent"
            >
              <span>⏹</span> Quit
            </button>
            <button
              onClick={sendInterrupt}
              disabled={!connected}
              className={classNames(
                "flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm disabled:opacity-50 min-h-[44px] transition-colors",
                isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-white hover:bg-gray-50 text-gray-700 border border-gray-300"
              )}
              title="Send Ctrl+C to interrupt"
              aria-label="Send interrupt signal"
            >
              ⌃C
            </button>
            <button
              onClick={onRelaunch}
              disabled={isBusy}
              className={classNames(
                "flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm disabled:opacity-50 min-h-[44px] transition-colors",
                isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-white hover:bg-gray-50 text-gray-700 border border-gray-300"
              )}
              aria-label="Relaunch agent"
            >
              <span>🔄</span> Relaunch
            </button>
          </>
        ) : (
          <>
            <button
              onClick={onLaunch}
              disabled={isBusy}
              className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-emerald-700 hover:bg-emerald-600 text-white text-sm disabled:opacity-50 min-h-[44px] transition-colors"
              aria-label="Launch agent"
            >
              <span>▶</span> Launch
            </button>
            <button
              onClick={onEdit}
              disabled={isBusy}
              className={classNames(
                "flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm disabled:opacity-50 min-h-[44px] transition-colors",
                isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-white hover:bg-gray-50 text-gray-700 border border-gray-300"
              )}
              aria-label="Edit agent configuration"
            >
              <span>✏️</span> Edit
            </button>
          </>
        )}
        <button
          onClick={onInbox}
          className={classNames(
            "flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm min-h-[44px] transition-colors",
            unreadCount > 0
              ? "bg-rose-900/30 text-rose-300 hover:bg-rose-900/50"
              : isDark 
                ? "bg-slate-800 hover:bg-slate-700 text-slate-200" 
                : "bg-white hover:bg-gray-50 text-gray-700 border border-gray-300"
          )}
          aria-label={`Open inbox${unreadCount > 0 ? `, ${unreadCount} unread messages` : ""}`}
        >
          <span>📥</span> Inbox
          {unreadCount > 0 && (
            <span className="bg-rose-500 text-white text-[10px] px-1.5 py-0.5 rounded-full font-medium" aria-hidden="true">
              {unreadCount > 99 ? "99+" : unreadCount}
            </span>
          )}
        </button>
        <div className="flex-1" />
        <button
          onClick={onRemove}
          disabled={isBusy || isRunning}
          className={classNames(
            "flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm disabled:opacity-50 min-h-[44px] transition-colors",
            isDark ? "hover:bg-rose-900/30 text-rose-400" : "hover:bg-rose-50 text-rose-600"
          )}
          title={isRunning ? "Stop the agent before removing" : "Remove agent"}
          aria-label="Remove agent"
        >
          <span>🗑</span> Remove
        </button>
      </div>
    </div>
  );
}
