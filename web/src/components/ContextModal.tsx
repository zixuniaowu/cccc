import { useState } from "react";
import { GroupContext } from "../types";

function classNames(...xs: Array<string | false | null | undefined>) {
  return xs.filter(Boolean).join(" ");
}

interface ContextModalProps {
  isOpen: boolean;
  onClose: () => void;
  context: GroupContext | null;
  onUpdateVision: (vision: string) => Promise<void>;
  onUpdateSketch: (sketch: string) => Promise<void>;
  busy: boolean;
  isDark: boolean;
}

export function ContextModal({
  isOpen,
  onClose,
  context,
  onUpdateVision,
  onUpdateSketch,
  busy,
  isDark,
}: ContextModalProps) {
  const [editingVision, setEditingVision] = useState(false);
  const [editingSketch, setEditingSketch] = useState(false);
  const [visionText, setVisionText] = useState("");
  const [sketchText, setSketchText] = useState("");

  if (!isOpen) return null;

  const handleEditVision = () => {
    setVisionText(context?.vision || "");
    setEditingVision(true);
  };

  const handleSaveVision = async () => {
    await onUpdateVision(visionText);
    setEditingVision(false);
  };

  const handleEditSketch = () => {
    setSketchText(context?.sketch || "");
    setEditingSketch(true);
  };

  const handleSaveSketch = async () => {
    await onUpdateSketch(sketchText);
    setEditingSketch(false);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-fade-in">
      {/* Backdrop */}
      <div 
        className={isDark ? "absolute inset-0 bg-black/60" : "absolute inset-0 bg-black/40"} 
        onClick={onClose}
        aria-hidden="true"
      />
      
      {/* Modal */}
      <div 
        className={`relative rounded-xl border shadow-2xl w-full max-w-2xl max-h-[80vh] flex flex-col animate-scale-in ${
          isDark 
            ? "bg-slate-900 border-slate-700" 
            : "bg-white border-gray-200"
        }`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="context-modal-title"
      >
        {/* Header */}
        <div className={`flex items-center justify-between px-5 py-4 border-b ${
          isDark ? "border-slate-800" : "border-gray-200"
        }`}>
          <h2 id="context-modal-title" className={`text-lg font-semibold ${isDark ? "text-slate-100" : "text-gray-900"}`}>
            📋 Project Context
          </h2>
          <button
            onClick={onClose}
            className={`text-xl leading-none min-w-[44px] min-h-[44px] flex items-center justify-center rounded-lg transition-colors ${
              isDark ? "text-slate-400 hover:text-slate-200 hover:bg-slate-800" : "text-gray-400 hover:text-gray-600 hover:bg-gray-100"
            }`}
            aria-label="Close context modal"
          >
            ×
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5 space-y-6">
          {/* Vision */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <h3 className={`text-sm font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>Vision</h3>
              {!editingVision && (
                <button
                  onClick={handleEditVision}
                  className={`text-xs min-h-[36px] px-2 rounded transition-colors ${
                    isDark ? "text-slate-400 hover:text-slate-200" : "text-gray-500 hover:text-gray-700"
                  }`}
                >
                  ✏️ Edit
                </button>
              )}
            </div>
            {editingVision ? (
              <div className="space-y-2">
                <textarea
                  value={visionText}
                  onChange={(e) => setVisionText(e.target.value)}
                  className={`w-full h-32 px-3 py-2 border rounded-lg text-sm resize-none transition-colors ${
                    isDark 
                      ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500" 
                      : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                  }`}
                  placeholder="Describe the project vision..."
                />
                <div className="flex gap-2">
                  <button
                    onClick={handleSaveVision}
                    disabled={busy}
                    className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm rounded-lg disabled:opacity-50 min-h-[44px] transition-colors"
                  >
                    {busy ? "Saving..." : "Save"}
                  </button>
                  <button
                    onClick={() => setEditingVision(false)}
                    className={`px-4 py-2 text-sm rounded-lg min-h-[44px] transition-colors ${
                      isDark 
                        ? "bg-slate-700 hover:bg-slate-600 text-slate-200" 
                        : "bg-gray-100 hover:bg-gray-200 text-gray-700"
                    }`}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div className={`px-3 py-2 rounded-lg text-sm whitespace-pre-wrap min-h-[60px] ${
                isDark ? "bg-slate-800/50 text-slate-300" : "bg-gray-50 text-gray-700"
              }`}>
                {context?.vision || <span className={isDark ? "text-slate-500 italic" : "text-gray-400 italic"}>No vision set</span>}
              </div>
            )}
          </div>

          {/* Sketch */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <h3 className={`text-sm font-medium ${isDark ? "text-slate-300" : "text-gray-700"}`}>Sketch</h3>
              {!editingSketch && (
                <button
                  onClick={handleEditSketch}
                  className={`text-xs min-h-[36px] px-2 rounded transition-colors ${
                    isDark ? "text-slate-400 hover:text-slate-200" : "text-gray-500 hover:text-gray-700"
                  }`}
                >
                  ✏️ Edit
                </button>
              )}
            </div>
            {editingSketch ? (
              <div className="space-y-2">
                <textarea
                  value={sketchText}
                  onChange={(e) => setSketchText(e.target.value)}
                  className={`w-full h-32 px-3 py-2 border rounded-lg text-sm resize-none transition-colors ${
                    isDark 
                      ? "bg-slate-800 border-slate-700 text-slate-200 focus:border-slate-500" 
                      : "bg-white border-gray-300 text-gray-900 focus:border-blue-500"
                  }`}
                  placeholder="Technical sketch or architecture notes..."
                />
                <div className="flex gap-2">
                  <button
                    onClick={handleSaveSketch}
                    disabled={busy}
                    className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm rounded-lg disabled:opacity-50 min-h-[44px] transition-colors"
                  >
                    {busy ? "Saving..." : "Save"}
                  </button>
                  <button
                    onClick={() => setEditingSketch(false)}
                    className={`px-4 py-2 text-sm rounded-lg min-h-[44px] transition-colors ${
                      isDark 
                        ? "bg-slate-700 hover:bg-slate-600 text-slate-200" 
                        : "bg-gray-100 hover:bg-gray-200 text-gray-700"
                    }`}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div className={`px-3 py-2 rounded-lg text-sm whitespace-pre-wrap min-h-[60px] ${
                isDark ? "bg-slate-800/50 text-slate-300" : "bg-gray-50 text-gray-700"
              }`}>
                {context?.sketch || <span className={isDark ? "text-slate-500 italic" : "text-gray-400 italic"}>No sketch set</span>}
              </div>
            )}
          </div>

          {/* Milestones */}
          <div>
            <h3 className={`text-sm font-medium mb-2 ${isDark ? "text-slate-300" : "text-gray-700"}`}>Milestones</h3>
            {context?.milestones && context.milestones.length > 0 ? (
              <div className="space-y-2">
                {context.milestones.map((m) => (
                  <div key={m.id} className={`flex items-center gap-3 px-3 py-2 rounded-lg ${
                    isDark ? "bg-slate-800/50" : "bg-gray-50"
                  }`}>
                    <span className={classNames(
                      "text-xs px-2 py-0.5 rounded",
                      m.status === "done" 
                        ? "bg-emerald-900/50 text-emerald-300 dark:bg-emerald-900/50 dark:text-emerald-300" 
                        : m.status === "in_progress" 
                          ? "bg-blue-900/50 text-blue-300 dark:bg-blue-900/50 dark:text-blue-300" 
                          : isDark ? "bg-slate-700 text-slate-400" : "bg-gray-200 text-gray-600"
                    )}>
                      {m.status || "pending"}
                    </span>
                    <span className={`text-sm ${isDark ? "text-slate-200" : "text-gray-800"}`}>{m.title}</span>
                    {m.due && <span className={`text-xs ml-auto ${isDark ? "text-slate-500" : "text-gray-500"}`}>{m.due}</span>}
                  </div>
                ))}
              </div>
            ) : (
              <div className={`px-3 py-2 rounded-lg text-sm italic ${
                isDark ? "bg-slate-800/50 text-slate-500" : "bg-gray-50 text-gray-400"
              }`}>
                No milestones
              </div>
            )}
          </div>

          {/* Tasks */}
          <div>
            <h3 className={`text-sm font-medium mb-2 ${isDark ? "text-slate-300" : "text-gray-700"}`}>Tasks</h3>
            {context?.tasks && context.tasks.length > 0 ? (
              <div className="space-y-2">
                {context.tasks.map((t) => (
                  <div key={t.id} className={`flex items-center gap-3 px-3 py-2 rounded-lg ${
                    isDark ? "bg-slate-800/50" : "bg-gray-50"
                  }`}>
                    <span className={classNames(
                      "text-xs px-2 py-0.5 rounded",
                      t.status === "done" 
                        ? "bg-emerald-900/50 text-emerald-300" 
                        : t.status === "in_progress" 
                          ? "bg-blue-900/50 text-blue-300" 
                          : isDark ? "bg-slate-700 text-slate-400" : "bg-gray-200 text-gray-600"
                    )}>
                      {t.status || "pending"}
                    </span>
                    <span className={`text-sm ${isDark ? "text-slate-200" : "text-gray-800"}`}>{t.title}</span>
                    {t.assignee && (
                      <span className={`text-xs ml-auto ${isDark ? "text-slate-500" : "text-gray-500"}`}>→ {t.assignee}</span>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className={`px-3 py-2 rounded-lg text-sm italic ${
                isDark ? "bg-slate-800/50 text-slate-500" : "bg-gray-50 text-gray-400"
              }`}>
                No tasks
              </div>
            )}
          </div>

          {/* Notes */}
          <div>
            <h3 className={`text-sm font-medium mb-2 ${isDark ? "text-slate-300" : "text-gray-700"}`}>Notes</h3>
            {context?.notes && context.notes.length > 0 ? (
              <div className="space-y-2">
                {context.notes.map((n) => (
                  <div key={n.id} className={`px-3 py-2 rounded-lg ${isDark ? "bg-slate-800/50" : "bg-gray-50"}`}>
                    <div className={`text-sm font-medium ${isDark ? "text-slate-200" : "text-gray-800"}`}>{n.title}</div>
                    {n.content && (
                      <div className={`text-xs mt-1 whitespace-pre-wrap ${isDark ? "text-slate-400" : "text-gray-500"}`}>
                        {n.content.slice(0, 200)}{n.content.length > 200 ? "..." : ""}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className={`px-3 py-2 rounded-lg text-sm italic ${
                isDark ? "bg-slate-800/50 text-slate-500" : "bg-gray-50 text-gray-400"
              }`}>
                No notes
              </div>
            )}
          </div>

          {/* References */}
          <div>
            <h3 className={`text-sm font-medium mb-2 ${isDark ? "text-slate-300" : "text-gray-700"}`}>References</h3>
            {context?.references && context.references.length > 0 ? (
              <div className="space-y-2">
                {context.references.map((r) => (
                  <div key={r.id} className={`px-3 py-2 rounded-lg ${isDark ? "bg-slate-800/50" : "bg-gray-50"}`}>
                    <div className="flex items-center gap-2">
                      <span className={`text-xs px-1.5 py-0.5 rounded ${isDark ? "bg-slate-700 text-slate-400" : "bg-gray-200 text-gray-600"}`}>
                        {r.id}
                      </span>
                      <a 
                        href={r.url} 
                        target="_blank" 
                        rel="noopener noreferrer"
                        className={`text-sm truncate hover:underline ${isDark ? "text-blue-400" : "text-blue-600"}`}
                        title={r.url}
                      >
                        {r.title || r.url}
                      </a>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className={`px-3 py-2 rounded-lg text-sm italic ${
                isDark ? "bg-slate-800/50 text-slate-500" : "bg-gray-50 text-gray-400"
              }`}>
                No references
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
