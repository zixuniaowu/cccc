import puppeteer from 'puppeteer';
import { mkdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const outDir = join(__dirname, '..', 'docs', 'screenshots');
mkdirSync(outDir, { recursive: true });

const URL = 'http://localhost:5173/ui/eyes';

async function main() {
  const browser = await puppeteer.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  // ── Desktop view (1440x900) ──
  console.log('Capturing desktop view...');
  const desktopPage = await browser.newPage();
  await desktopPage.setViewport({ width: 1440, height: 900, deviceScaleFactor: 2 });
  await desktopPage.goto(URL, { waitUntil: 'networkidle2', timeout: 15000 });
  // Wait for Canvas2D eyes to render
  await new Promise(r => setTimeout(r, 3000));
  await desktopPage.screenshot({
    path: join(outDir, 'desktop-full.png'),
    fullPage: true,
  });
  // Just the eyes area
  await desktopPage.screenshot({
    path: join(outDir, 'desktop-viewport.png'),
    fullPage: false,
  });
  console.log('Desktop screenshots saved.');

  // ── Mobile view (390x844, iPhone-like) ──
  console.log('Capturing mobile view...');
  const mobilePage = await browser.newPage();
  await mobilePage.setViewport({ width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
  await mobilePage.setUserAgent(
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
  );
  await mobilePage.goto(URL, { waitUntil: 'networkidle2', timeout: 15000 });
  await new Promise(r => setTimeout(r, 3000));
  await mobilePage.screenshot({
    path: join(outDir, 'mobile-companion.png'),
    fullPage: false,
  });
  console.log('Mobile screenshot saved.');

  // ── Desktop eyes close-up (crop just the eyes pair) ──
  console.log('Capturing eyes close-up...');
  const eyesPage = await browser.newPage();
  await eyesPage.setViewport({ width: 1000, height: 500, deviceScaleFactor: 2 });
  await eyesPage.goto(URL, { waitUntil: 'networkidle2', timeout: 15000 });
  await new Promise(r => setTimeout(r, 3000));

  const eyesPair = await eyesPage.$('.eyes-pair');
  if (eyesPair) {
    await eyesPair.screenshot({ path: join(outDir, 'eyes-closeup.png') });
    console.log('Eyes close-up saved.');
  } else {
    console.log('Could not find .eyes-pair element');
  }

  await browser.close();
  console.log('All screenshots captured in', outDir);
}

main().catch(e => { console.error(e); process.exit(1); });
