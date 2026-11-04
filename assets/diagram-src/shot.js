const { chromium } = require('playwright');
(async () => {
  const [,, html, out] = process.argv;
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1200, height: 800 }, deviceScaleFactor: 2 });
  await p.goto('file://' + html);
  await p.evaluate(() => document.fonts.ready);
  const el = await p.$('#root');
  const box = await el.boundingBox();
  // report overflow of rows
  const ov = await p.evaluate(() => [...document.querySelectorAll('.row,.lane')].map(r => r.scrollWidth - r.clientWidth).filter(x => x > 0));
  console.log('box', JSON.stringify(box), 'overflow', JSON.stringify(ov));
  await el.screenshot({ path: out });
  await b.close();
})();
