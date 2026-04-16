import { test, expect } from '@playwright/test';

test.describe('Game Detail Flow - End to End', () => {
  const BASE_URL = 'http://localhost:3000';
  let gameSlug: string;

  test.beforeEach(async ({ page }) => {
    // Set default viewport to desktop
    await page.setViewportSize({ width: 1920, height: 1080 });
  });

  test('should navigate from home to game detail and verify all content', async ({ page }) => {
    // Step 1: Navigate to Home Page
    await test.step('Navigate to Home Page', async () => {
      await page.goto(BASE_URL);
      
      // Verify page loads successfully
      await expect(page).toHaveTitle(/WhatIsMyTip/);
      
      // Wait for content to load
      await page.waitForLoadState('networkidle');
      
      // Verify hero section is displayed
      await expect(page.locator('.hero h1')).toContainText('AI-Powered');
      await expect(page.locator('.hero p')).toContainText('Smart heuristics');
    });

    // Step 2: Verify game cards are displayed
    await test.step('Verify game cards are displayed', async () => {
      // Wait for games to load
      await page.waitForSelector('.games-grid', { timeout: 10000 });
      
      // Verify game cards exist
      const gameCards = page.locator('.game-card');
      const count = await gameCards.count();
      expect(count).toBeGreaterThan(0);
      
      // Verify tips are shown on game cards
      const firstCard = gameCards.first();
      await expect(firstCard.locator('.tip-info')).toBeVisible();
    });

    // Step 3: Click on first game card
    await test.step('Click on first game card', async () => {
      const firstCardLink = page.locator('.game-card-link').first();
      
      // Get the game slug from the href
      const href = await firstCardLink.getAttribute('href');
      expect(href).toMatch(/\/game\/[a-zA-Z0-9]+/);
      gameSlug = href!.split('/').pop()!;
      
      // Click on the game card
      await firstCardLink.click();
      
      // Verify navigation to game detail page
      await expect(page).toHaveURL(`${BASE_URL}/game/${gameSlug}`);
    });

    // Step 4: Verify Game Detail Page Content
    await test.step('Verify game information is displayed', async () => {
      // Wait for content to load
      await page.waitForSelector('.game-detail-page .content', { timeout: 10000 });
      
      // Verify back link
      await expect(page.locator('.back-link')).toContainText('Back to Home');
      
      // Verify game info section
      await expect(page.locator('.round')).toBeVisible();
      await expect(page.locator('.season')).toBeVisible();
      await expect(page.locator('.status')).toBeVisible();
      
      // Verify teams are displayed
      await expect(page.locator('.team.home .team-logo')).toBeVisible();
      await expect(page.locator('.team.away .team-logo')).toBeVisible();
      await expect(page.locator('.team.home .team-name')).toBeVisible();
      await expect(page.locator('.team.away .team-name')).toBeVisible();
      
      // Verify venue and date
      await expect(page.locator('.game-meta')).toBeVisible();
      await expect(page.locator('.game-meta')).toContainText('Venue:');
      await expect(page.locator('.game-meta')).toContainText('Date:');
    });

    // Step 5: Verify all 3 heuristic tips are shown
    await test.step('Verify all 3 heuristic tips are shown', async () => {
      await expect(page.locator('.tips-section')).toBeVisible();
      await expect(page.locator('.tips-section .section-title')).toContainText('Heuristic Tips');
      
      const tipsGrid = page.locator('.tips-grid');
      await expect(tipsGrid).toBeVisible();
      
      // Verify 3 tips are displayed
      const tipCards = tipsGrid.locator('.tip-card');
      const tipCount = await tipCards.count();
      expect(tipCount).toBe(3);
      
      // Verify each tip has required elements
      for (let i = 0; i < tipCount; i++) {
        const tip = tipCards.nth(i);
        await expect(tip.locator('.tip-header')).toBeVisible();
        await expect(tip.locator('.heuristic-badge')).toBeVisible();
        await expect(tip.locator('.confidence')).toBeVisible();
        await expect(tip.locator('.tip-body h3')).toBeVisible();
        await expect(tip.locator('.margin')).toBeVisible();
        await expect(tip.locator('.explanation')).toBeVisible();
      }
    });

    // Step 6: Verify all 4 model predictions are shown
    await test.step('Verify all 4 model predictions are shown', async () => {
      await expect(page.locator('.models-section')).toBeVisible();
      await expect(page.locator('.models-section .section-title')).toContainText('Model Predictions');
      
      const modelsGrid = page.locator('.models-grid');
      await expect(modelsGrid).toBeVisible();
      
      // Verify 4 model predictions are displayed
      const modelCards = modelsGrid.locator('.model-card');
      const modelCount = await modelCards.count();
      expect(modelCount).toBe(4);
      
      // Verify each model has required elements
      for (let i = 0; i < modelCount; i++) {
        const model = modelCards.nth(i);
        await expect(model.locator('.model-header')).toBeVisible();
        await expect(model.locator('.model-name')).toBeVisible();
        await expect(model.locator('.confidence')).toBeVisible();
        await expect(model.locator('.model-body h3')).toBeVisible();
        await expect(model.locator('.margin')).toBeVisible();
      }
      
      // Verify specific model names are displayed
      const modelNames = await modelsGrid.locator('.model-name').allTextContents();
      const expectedModels = ['Elo Rating', 'Form', 'Home Advantage', 'Value'];
      for (const expectedModel of expectedModels) {
        expect(modelNames.some(name => name.includes(expectedModel))).toBeTruthy();
      }
    });
  });

  test('should navigate back to home page', async ({ page }) => {
    // Navigate to home and click first game
    await page.goto(BASE_URL);
    await page.waitForSelector('.games-grid', { timeout: 10000 });
    
    const firstCardLink = page.locator('.game-card-link').first();
    await firstCardLink.click();
    
    // Wait for game detail page
    await page.waitForSelector('.game-detail-page .content', { timeout: 10000 });
    
    // Click back link
    await page.click('.back-link');
    
    // Verify return to home page
    await expect(page).toHaveURL(BASE_URL);
    await expect(page.locator('.hero h1')).toContainText('AI-Powered');
  });

  test('should display content correctly on mobile viewport', async ({ page }) => {
    // Set mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });
    
    await page.goto(BASE_URL);
    await page.waitForSelector('.games-grid', { timeout: 10000 });
    
    // Click first game
    await page.locator('.game-card-link').first().click();
    
    // Wait for game detail page
    await page.waitForSelector('.game-detail-page .content', { timeout: 10000 });
    
    // Verify content is displayed on mobile
    await expect(page.locator('.game-info')).toBeVisible();
    await expect(page.locator('.tips-section')).toBeVisible();
    await expect(page.locator('.models-section')).toBeVisible();
    
    // Take screenshot for mobile
    await page.screenshot({ path: 'test-screenshots/mobile-game-detail.png' });
  });

  test('should display content correctly on tablet viewport', async ({ page }) => {
    // Set tablet viewport
    await page.setViewportSize({ width: 768, height: 1024 });
    
    await page.goto(BASE_URL);
    await page.waitForSelector('.games-grid', { timeout: 10000 });
    
    // Click first game
    await page.locator('.game-card-link').first().click();
    
    // Wait for game detail page
    await page.waitForSelector('.game-detail-page .content', { timeout: 10000 });
    
    // Verify content is displayed on tablet
    await expect(page.locator('.game-info')).toBeVisible();
    await expect(page.locator('.tips-section')).toBeVisible();
    await expect(page.locator('.models-section')).toBeVisible();
    
    // Take screenshot for tablet
    await page.screenshot({ path: 'test-screenshots/tablet-game-detail.png' });
  });

  test('should display content correctly on desktop viewport', async ({ page }) => {
    // Set desktop viewport
    await page.setViewportSize({ width: 1920, height: 1080 });
    
    await page.goto(BASE_URL);
    await page.waitForSelector('.games-grid', { timeout: 10000 });
    
    // Click first game
    await page.locator('.game-card-link').first().click();
    
    // Wait for game detail page
    await page.waitForSelector('.game-detail-page .content', { timeout: 10000 });
    
    // Verify content is displayed on desktop
    await expect(page.locator('.game-info')).toBeVisible();
    await expect(page.locator('.tips-section')).toBeVisible();
    await expect(page.locator('.models-section')).toBeVisible();
    
    // Take screenshot for desktop
    await page.screenshot({ path: 'test-screenshots/desktop-game-detail.png' });
  });

  test('should handle error states gracefully', async ({ page }) => {
    // Navigate to a non-existent game
    await page.goto(`${BASE_URL}/game/invalidslug123`);
    
    // Wait for error state
    await page.waitForSelector('.error', { timeout: 10000 });
    
    // Verify error message is displayed
    await expect(page.locator('.error')).toBeVisible();
    await expect(page.locator('.error h2')).toContainText('Error');
    
    // Verify back link is present
    await expect(page.locator('.back-link')).toBeVisible();
  });
});

// Configure Playwright
test.use({
  baseURL: 'http://localhost:3000',
  screenshot: 'only-on-failure',
  video: 'retain-on-failure',
  trace: 'retain-on-failure',
});
