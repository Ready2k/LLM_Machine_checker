const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch();
  const page = await browser.newPage();
  
  page.on('console', msg => console.log('PAGE LOG:', msg.text()));
  page.on('pageerror', error => console.log('PAGE ERROR:', error.message));
  page.on('requestfailed', request => console.log('REQUEST FAILED:', request.url(), request.failure().errorText));
  
  await page.goto('http://127.0.0.1:8080');
  await new Promise(r => setTimeout(r, 2000));
  
  const archPanelHtml = await page.evaluate(() => {
    const p = document.getElementById('arch-panel');
    return p ? p.innerHTML : 'NULL';
  });
  console.log('Arch Panel:', archPanelHtml);
  
  await browser.close();
})();
