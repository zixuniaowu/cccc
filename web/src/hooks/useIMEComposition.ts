// useIMEComposition — Fix IME composition interruption in controlled inputs.
//
// Problem: Zustand uses useSyncExternalStore which triggers synchronous
// re-renders, bypassing React 18's auto-batching. During IME composition,
// this causes the controlled value to overwrite the DOM mid-composition,
// breaking CJK input.
//
// Solution: Maintain a local state that tracks the input value independently.
// During composition, the local state is decoupled from external state so
// re-renders cannot overwrite the DOM. On compositionEnd, the final composed
// value is flushed to the external onChange handler.

import { useCallback, useEffect, useRef, useState } from "react";

type InputElement = HTMLInputElement | HTMLTextAreaElement;

interface UseIMECompositionOptions {
  /** The controlled value from external state (e.g. Zustand). */
  value: string;
  /** The external setter — called only outside composition or on compositionend. */
  onChange: (value: string) => void;
}

interface UseIMECompositionReturn {
  /** Bind this to the element's value prop. Returns local state, immune to external re-renders during composition. */
  value: string;
  /** Bind to onChange. */
  onChange: (e: React.ChangeEvent<InputElement>) => void;
  /** Bind to onCompositionStart. */
  onCompositionStart: () => void;
  /** Bind to onCompositionEnd. */
  onCompositionEnd: (e: React.CompositionEvent<InputElement>) => void;
}

export function useIMEComposition({
  value,
  onChange,
}: UseIMECompositionOptions): UseIMECompositionReturn {
  const [localValue, setLocalValue] = useState(value);
  const composingRef = useRef(false);

  // Sync local state from external value when not composing.
  // This handles programmatic resets and normal external updates.
  useEffect(() => {
    if (!composingRef.current) {
      setLocalValue(value);
    }
  }, [value]);

  const handleChange = useCallback(
    (e: React.ChangeEvent<InputElement>) => {
      const nextValue = e.target.value;
      setLocalValue(nextValue);

      if (!composingRef.current) {
        // Not composing: propagate to external state immediately.
        onChange(nextValue);
      }
      // Composing: only update local state. External state stays untouched
      // until compositionEnd flushes the final value.
    },
    [onChange],
  );

  const handleCompositionStart = useCallback(() => {
    composingRef.current = true;
  }, []);

  const handleCompositionEnd = useCallback(
    (e: React.CompositionEvent<InputElement>) => {
      composingRef.current = false;
      // Flush the final composed value to external state.
      const finalValue = e.currentTarget.value;
      setLocalValue(finalValue);
      onChange(finalValue);
    },
    [onChange],
  );

  return {
    value: localValue,
    onChange: handleChange,
    onCompositionStart: handleCompositionStart,
    onCompositionEnd: handleCompositionEnd,
  };
}
