import puppeteer from 'puppeteer';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const reportPath = `file:///${join(__dirname, '..', 'docs', 'voice-agent-upgrade-report.html').replace(/\\/g, '/')}`;

async function main() {
  const browser = await puppeteer.launch({ headless: true, args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1200, height: 800, deviceScaleFactor: 2 });
  await page.goto(reportPath, { waitUntil: 'networkidle2' });
  await new Promise(r => setTimeout(r, 1000));
  await page.screenshot({
    path: join(__dirname, '..', 'docs', 'report-preview.png'),
    fullPage: true,
  });
  console.log('Report preview saved.');
  await browser.close();
}

main().catch(e => { console.error(e); process.exit(1); });
