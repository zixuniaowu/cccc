import puppeteer from 'puppeteer';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const URL = 'https://github.com/zixuniaowu/cccc/tree/feat/voice-agent';

async function main() {
  const browser = await puppeteer.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 2 });

  console.log('Navigating to GitHub...');
  await page.goto(URL, { waitUntil: 'networkidle2', timeout: 30000 });

  // Wait for README to render
  await new Promise(r => setTimeout(r, 4000));

  // Full page screenshot
  const outPath = join(__dirname, '..', 'docs', 'github-preview.png');
  await page.screenshot({ path: outPath, fullPage: true });
  console.log('Full page saved:', outPath);

  // Viewport-only screenshot
  const outPath2 = join(__dirname, '..', 'docs', 'github-viewport.png');
  await page.screenshot({ path: outPath2, fullPage: false });
  console.log('Viewport saved:', outPath2);

  await browser.close();
}

main().catch(e => { console.error(e); process.exit(1); });
