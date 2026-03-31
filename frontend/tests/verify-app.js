import { chromium } from 'playwright';

(async () => {
  const consoleErrors = [];
  const networkFailures = [];
  const apiResponses = [];

  console.log('Starting application verification...\n');

  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext();
  const page = await context.newPage();

  // Collect console errors
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      consoleErrors.push(msg.text());
      console.log(`[Console Error] ${msg.text()}`);
    }
  });

  // Collect network failures
  page.on('response', (response) => {
    if (response.status() >= 400) {
      networkFailures.push(`${response.url()} - ${response.status()}`);
      console.log(`[Network Error] ${response.url()} - ${response.status()}`);
    }
    if (response.url().includes('/api/')) {
      apiResponses.push({
        url: response.url(),
        status: response.status(),
        ok: response.ok()
      });
    }
  });

  try {
    console.log('=== Step 1: Testing Home Page ===');
    await page.goto('http://localhost:3000', { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);

    // Take screenshot of home page
    await page.screenshot({ path: 'frontend/verification-home.png', fullPage: true });
    console.log('Screenshot saved: frontend/verification-home.png');

    // Check page title
    const title = await page.title();
    console.log(`Page title: ${title}`);

    // Check for game cards
    const gameCards = await page.locator('.game-card').count();
    console.log(`Found ${gameCards} game cards`);

    // Check for tip cards
    const tipCards = await page.locator('.tip-card').count();
    console.log(`Found ${tipCards} tip cards`);

    // Check for error elements
    const errorElements = await page.locator('[class*="error"], [class*="Error"]').count();
    console.log(`Found ${errorElements} error elements`);

    console.log('\n=== Step 2: Testing Game Detail Page ===');
    
    // Try to find and click on a game card
    const firstGameCard = page.locator('.game-card').first();
    const cardCount = await firstGameCard.count();

    if (cardCount > 0) {
      console.log('Found game card, attempting to navigate...');
      
      const gameLink = firstGameCard.locator('a').first();
      const href = await gameLink.getAttribute('href');
      console.log(`Game link href: ${href}`);

      if (href) {
        await page.goto(`http://localhost:3000${href}`, { waitUntil: 'networkidle' });
        await page.waitForTimeout(2000);

        await page.screenshot({ path: 'frontend/verification-game-detail.png', fullPage: true });
        console.log('Screenshot saved: frontend/verification-game-detail.png');

        const gameInfo = await page.locator('[class*="game"], [class*="match"]').count();
        console.log(`Found ${gameInfo} game info elements`);

        const tipsOnDetail = await page.locator('[class*="tip"]').count();
        console.log(`Found ${tipsOnDetail} tip elements on detail page`);
      }
    } else {
      console.log('No game cards found, trying direct navigation...');
      await page.goto('http://localhost:3000/game/245', { waitUntil: 'networkidle' });
      await page.waitForTimeout(2000);

      await page.screenshot({ path: 'frontend/verification-game-detail-direct.png', fullPage: true });
      console.log('Screenshot saved: frontend/verification-game-detail-direct.png');

      const pageTitle = await page.title();
      console.log(`Game detail page title: ${pageTitle}`);
    }

    console.log('\n=== Step 3: API Connectivity Test ===');
    
    // Test direct API call via fetch
    const apiTestResult = await page.evaluate(async () => {
      try {
        const response = await fetch('http://localhost:8000/api/games');
        const data = await response.json();
        return { success: true, status: response.status, count: data.length || 0 };
      } catch (error) {
        return { success: false, error: error.message };
      }
    });

    console.log('Direct API test result:', JSON.stringify(apiTestResult, null, 2));

    console.log('\n=== Summary ===');
    console.log(`Console errors: ${consoleErrors.length}`);
    console.log(`Network failures: ${networkFailures.length}`);
    console.log(`API responses captured: ${apiResponses.length}`);

    if (apiResponses.length > 0) {
      console.log('\nAPI Responses:');
      apiResponses.forEach(resp => {
        console.log(`  ${resp.url.substring(0, 80)}... - Status: ${resp.status}, OK: ${resp.ok}`);
      });
    }

    if (consoleErrors.length > 0) {
      console.log('\n=== Console Errors ===');
      consoleErrors.forEach(err => console.log(`  - ${err}`));
    }

    if (networkFailures.length > 0) {
      console.log('\n=== Network Failures ===');
      networkFailures.forEach(fail => console.log(`  - ${fail}`));
    }

    console.log('\n=== Verification Complete ===');

  } catch (error) {
    console.error('Error during verification:', error);
    await page.screenshot({ path: 'frontend/verification-error.png', fullPage: true });
  } finally {
    await browser.close();
  }

  console.log('\nPress Ctrl+C to exit...');
})();
