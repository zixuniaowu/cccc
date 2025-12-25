import { useEffect, useMemo, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";

type Props = {
  groupId: string;
  actorId: string;
  actorTitle?: string;
  onClose: () => void;
};

function wsUrl(path: string): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}${path}`;
}

export function TerminalModal({ groupId, actorId, actorTitle, onClose }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const decoderRef = useRef<TextDecoder | null>(null);

  const [status, setStatus] = useState<"connecting" | "connected" | "closed" | "error">("connecting");
  const title = useMemo(() => (actorTitle ? `${actorId} · ${actorTitle}` : actorId), [actorId, actorTitle]);

  function sendInterrupt() {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    // Send Ctrl+C (ASCII 0x03)
    ws.send(JSON.stringify({ t: "i", d: "\x03" }));
  }

  function focusTerminal() {
    termRef.current?.focus();
  }

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    decoderRef.current = new TextDecoder("utf-8");

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily:
        'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
      theme: {
        background: "#0b0f17",
        foreground: "#e6edf3",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(el);
    fit.fit();

    termRef.current = term;
    fitRef.current = fit;

    const path = `/api/v1/groups/${encodeURIComponent(groupId)}/actors/${encodeURIComponent(actorId)}/term`;
    const ws = new WebSocket(wsUrl(path));
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    const sendResize = () => {
      const t = termRef.current;
      const w = wsRef.current;
      if (!t || !w || w.readyState !== WebSocket.OPEN) return;
      w.send(JSON.stringify({ t: "r", c: t.cols, r: t.rows }));
    };

    const fitAndResize = () => {
      try {
        fit.fit();
        sendResize();
      } catch {
        // ignore
      }
    };

    const ro = new ResizeObserver(() => fitAndResize());
    ro.observe(el);

    const onDataDisp = term.onData((data) => {
      try {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ t: "i", d: data }));
      } catch {
        // ignore
      }
    });

    ws.onopen = () => {
      setStatus("connected");
      fitAndResize();
      term.focus();
    };
    ws.onerror = () => setStatus("error");
    ws.onclose = () => setStatus("closed");
    ws.onmessage = (ev) => {
      const t = termRef.current;
      if (!t) return;
      if (typeof ev.data === "string") {
        t.write(`\r\n[server] ${ev.data}\r\n`);
        return;
      }
      try {
        const decoder = decoderRef.current || new TextDecoder("utf-8");
        decoderRef.current = decoder;
        const bytes = new Uint8Array(ev.data as ArrayBuffer);
        t.write(decoder.decode(bytes, { stream: true }));
      } catch {
        // ignore
      }
    };

    return () => {
      try {
        ro.disconnect();
      } catch {
        // ignore
      }
      try {
        onDataDisp.dispose();
      } catch {
        // ignore
      }
      try {
        ws.close();
      } catch {
        // ignore
      }
      try {
        term.dispose();
      } catch {
        // ignore
      }
      wsRef.current = null;
      termRef.current = null;
      fitRef.current = null;
      decoderRef.current = null;
    };
  }, [actorId, groupId]);

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-start justify-center p-6"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-full max-w-5xl rounded border border-slate-800 bg-slate-950/95 shadow-xl">
        <div className="px-4 py-3 border-b border-slate-800 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-semibold truncate">Terminal · {title}</div>
            <div className="text-xs text-slate-400">
              {status === "connected" ? "connected" : status === "connecting" ? "connecting…" : status}
            </div>
          </div>
          <div className="flex gap-2">
            <button
              className="rounded bg-rose-600 hover:bg-rose-500 text-white px-3 py-1 text-sm font-medium disabled:opacity-50"
              onClick={sendInterrupt}
              disabled={status !== "connected"}
              title="Send interrupt signal (Ctrl+C)"
            >
              Interrupt
            </button>
            <button
              className="rounded bg-slate-800 border border-slate-700 text-slate-200 px-3 py-1 text-sm font-medium"
              onClick={focusTerminal}
              title="Focus terminal input"
            >
              Focus
            </button>
            <button
              className="rounded bg-slate-200 text-slate-950 px-3 py-1 text-sm font-medium"
              onClick={onClose}
            >
              Close
            </button>
          </div>
        </div>

        <div className="p-3">
          <div
            ref={containerRef}
            className="h-[70vh] w-full rounded border border-slate-800 bg-[#0b0f17] overflow-hidden"
          />
        </div>
      </div>
    </div>
  );
}
