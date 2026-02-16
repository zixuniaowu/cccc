import { readFileSync, writeFileSync } from "node:fs";
import { resolve, dirname, join } from "node:path";

const htmlPath = resolve("C:/Users/zixun/dev/cccc/docs/voice-agent-upgrade-report.html");
const htmlDir = dirname(htmlPath);

let html = readFileSync(htmlPath, "utf-8");

// Match all src="screenshots/xxx.png" occurrences
const srcRegex = /src="(screenshots\/[^"]+\.png)"/g;

let match;
const seen = new Map(); // cache base64 per relative path

while ((match = srcRegex.exec(html)) !== null) {
  const relPath = match[1]; // e.g. "screenshots/eyes-closeup.png"
  if (!seen.has(relPath)) {
    const absPath = join(htmlDir, relPath);
    const buf = readFileSync(absPath);
    const b64 = buf.toString("base64");
    seen.set(relPath, `data:image/png;base64,${b64}`);
    console.log(`Encoded ${relPath} (${buf.length} bytes -> ${b64.length} base64 chars)`);
  }
}

// Replace all occurrences
for (const [relPath, dataUri] of seen) {
  // Use split+join for global replacement (avoids regex special char issues)
  html = html.split(`src="${relPath}"`).join(`src="${dataUri}"`);
}

writeFileSync(htmlPath, html, "utf-8");
console.log(`\nDone. Wrote ${html.length} chars back to ${htmlPath}`);
console.log(`Embedded ${seen.size} unique images across all <img> tags.`);
