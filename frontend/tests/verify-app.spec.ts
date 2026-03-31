import { test, expect } from '@playwright/test';

test.describe('Application Verification', () => {
  test('verify home page loads and works correctly', async ({ page }) => {
    // Collect console errors
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        consoleErrors.push(msg.text());
      }
    });

    // Collect network failures
    const networkFailures: string[] = [];
    page.on('response', (response) => {
      if (response.status() >= 400) {
        networkFailures.push(`${response.url()} - ${response.status()}`);
      }
    });

    // Navigate to home page
    await page.goto('http://localhost:3000', { waitUntil: 'networkidle' });
    
    // Take screenshot of home page
    await page.screenshot({ path: 'frontend/verification-home.png', fullPage: true });
    
    // Check page title
    const title = await page.title();
    console.log('Page title:', title);
    
    // Wait for page to load
    await page.waitForTimeout(2000);
    
    // Check if game cards are present
    const gameCards = await page.locator('.game-card').count();
    console.log(`Found ${gameCards} game cards`);
    
    // Check if tips are displayed
    const tipCards = await page.locator('.tip-card').count();
    console.log(`Found ${tipCards} tip cards`);
    
    // Check for any visible error messages
    const errorElements = await page.locator('[class*="error"], [class*="Error"]').count();
    console.log(`Found ${errorElements} error elements`);
    
    // Log console errors
    if (consoleErrors.length > 0) {
      console.log('Console errors found:', consoleErrors);
    } else {
      console.log('No console errors found');
    }
    
    // Log network failures
    if (networkFailures.length > 0) {
      console.log('Network failures found:', networkFailures);
    } else {
      console.log('No network failures found');
    }
    
    // Take a second screenshot after content loads
    await page.screenshot({ path: 'frontend/verification-home-loaded.png', fullPage: true });
  });

  test('verify game detail page navigation', async ({ page }) => {
    // Collect console errors
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        consoleErrors.push(msg.text());
      }
    });

    // Navigate to home page first
    await page.goto('http://localhost:3000', { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);
    
    // Try to find and click on a game card
    const firstGameCard = page.locator('.game-card').first();
    const cardCount = await firstGameCard.count();
    
    if (cardCount > 0) {
      console.log('Found game card, attempting to click...');
      
      // Get the href of the first game card
      const gameLink = firstGameCard.locator('a').first();
      const href = await gameLink.getAttribute('href');
      console.log('Game link href:', href);
      
      if (href) {
        // Navigate to the game detail page
        await page.goto(`http://localhost:3000${href}`, { waitUntil: 'networkidle' });
        await page.waitForTimeout(2000);
        
        // Take screenshot of game detail page
        await page.screenshot({ path: 'frontend/verification-game-detail.png', fullPage: true });
        
        // Check if game info is displayed
        const gameInfo = await page.locator('[class*="game"], [class*="match"]').count();
        console.log(`Found ${gameInfo} game info elements`);
        
        // Check if tips are displayed on detail page
        const tipsOnDetail = await page.locator('[class*="tip"]').count();
        console.log(`Found ${tipsOnDetail} tip elements on detail page`);
        
        // Log console errors
        if (consoleErrors.length > 0) {
          console.log('Console errors on game detail:', consoleErrors);
        } else {
          console.log('No console errors on game detail page');
        }
      } else {
        console.log('No href found on game card');
      }
    } else {
      console.log('No game cards found on home page');
      // Try navigating directly to a game detail page
      console.log('Trying direct navigation to game detail page...');
      await page.goto('http://localhost:3000/game/245', { waitUntil: 'networkidle' });
      await page.waitForTimeout(2000);
      
      await page.screenshot({ path: 'frontend/verification-game-detail-direct.png', fullPage: true });
      
      // Check if page loaded
      const pageTitle = await page.title();
      console.log('Game detail page title:', pageTitle);
      
      // Log console errors
      if (consoleErrors.length > 0) {
        console.log('Console errors on direct game detail:', consoleErrors);
      } else {
        console.log('No console errors on direct game detail page');
      }
    }
  });

  test('check API connectivity', async ({ page }) => {
    // Test direct API calls from browser
    const apiResponses: { url: string; status: number; ok: boolean }[] = [];
    
    page.on('response', async (response) => {
      if (response.url().includes('/api/')) {
        apiResponses.push({
          url: response.url(),
          status: response.status(),
          ok: response.ok()
        });
      }
    });
    
    // Navigate to home page
    await page.goto('http://localhost:3000', { waitUntil: 'networkidle' });
    await page.waitForTimeout(3000);
    
    console.log('API responses captured:');
    apiResponses.forEach(resp => {
      console.log(`  ${resp.url} - Status: ${resp.status}, OK: ${resp.ok}`);
    });
    
    // Check if any API calls failed
    const failedCalls = apiResponses.filter(r => !r.ok);
    if (failedCalls.length > 0) {
      console.log('Failed API calls:', failedCalls);
    } else {
      console.log('All API calls succeeded');
    }
    
    // Try direct API call via fetch
    const apiTestResult = await page.evaluate(async () => {
      try {
        const response = await fetch('http://localhost:8000/api/games');
        const data = await response.json();
        return { success: true, status: response.status, count: data.length || 0 };
      } catch (error) {
        return { success: false, error: (error as Error).message };
      }
    });
    
    console.log('Direct API test result:', apiTestResult);
  });
});
