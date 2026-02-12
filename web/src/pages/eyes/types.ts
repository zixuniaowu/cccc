export type Mood = "idle" | "listening" | "thinking" | "speaking" | "error";

export type LogLine = {
  who: "me" | "agent";
  text: string;
  ts: number;
};
