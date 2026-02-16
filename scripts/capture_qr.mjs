import puppeteer from 'puppeteer';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const URL = 'http://localhost:5173/ui/eyes';

async function main() {
  const browser = await puppeteer.launch({ headless: true, args: ['--no-sandbox'] });

  // Desktop — scroll to QR section
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 2 });
  await page.goto(URL, { waitUntil: 'networkidle2', timeout: 15000 });
  await new Promise(r => setTimeout(r, 3000));

  // Scroll down to find the QR code section
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await new Promise(r => setTimeout(r, 1000));

  await page.screenshot({
    path: join(__dirname, '..', 'docs', 'screenshots', 'desktop-qr.png'),
    fullPage: false,
  });
  console.log('Desktop QR screenshot saved.');

  await browser.close();
}

main().catch(e => { console.error(e); process.exit(1); });
