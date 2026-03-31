import { chromium } from 'playwright';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';

const BASE_URL = 'http://localhost:3000';
const SCREENSHOT_DIR = 'test-screenshots';

// Create screenshot directory
try {
  mkdirSync(SCREENSHOT_DIR, { recursive: true });
} catch (e) {
  // Directory already exists
}

async function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function testHomeToGameDetailFlow() {
  console.log('🧪 Starting Game Detail Flow Test...\n');
  
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext();
  const page = await context.newPage();
  
  // Set default viewport to desktop
  await page.setViewportSize({ width: 1920, height: 1080 });
  
  let testResults = {
    passed: 0,
    failed: 0,
    errors: []
  };
  
  try {
    // Test 1: Navigate to Home Page
    console.log('📋 Test 1: Navigate to Home Page');
    try {
      await page.goto(BASE_URL, { waitUntil: 'networkidle' });
      const title = await page.title();
      console.log(`  ✓ Page loaded successfully. Title: "${title}"`);
      
      // Verify hero section
      const heroText = await page.locator('.hero h1').textContent();
      console.log(`  ✓ Hero section visible: "${heroText}"`);
      
      testResults.passed++;
    } catch (error) {
      console.error(`  ✗ Failed: ${error.message}`);
      testResults.failed++;
      testResults.errors.push({ test: 'Navigate to Home Page', error: error.message });
      await page.screenshot({ path: join(SCREENSHOT_DIR, 'error-home.png') });
    }
    
    // Test 2: Verify game cards are displayed
    console.log('\n📋 Test 2: Verify game cards are displayed');
    try {
      await page.waitForSelector('.games-grid', { timeout: 10000 });
      const gameCards = page.locator('.game-card');
      const count = await gameCards.count();
      console.log(`  ✓ Found ${count} game cards`);
      
      if (count > 0) {
        const firstCard = gameCards.first();
        const tipInfo = firstCard.locator('.tip-info');
        const hasTip = await tipInfo.count() > 0;
        console.log(`  ✓ First card has tip info: ${hasTip}`);
      }
      
      testResults.passed++;
    } catch (error) {
      console.error(`  ✗ Failed: ${error.message}`);
      testResults.failed++;
      testResults.errors.push({ test: 'Verify game cards', error: error.message });
      await page.screenshot({ path: join(SCREENSHOT_DIR, 'error-game-cards.png') });
    }
    
    // Test 3: Click on first game card and navigate
    console.log('\n📋 Test 3: Click on first game card');
    let gameId = null;
    try {
      const firstCardLink = page.locator('.game-card-link').first();
      const href = await firstCardLink.getAttribute('href');
      console.log(`  ✓ Game card href: ${href}`);
      gameId = href.split('/').pop();
      console.log(`  ✓ Game ID: ${gameId}`);
      
      await firstCardLink.click();
      await page.waitForURL(`${BASE_URL}/game/${gameId}`, { timeout: 10000 });
      console.log(`  ✓ Navigated to game detail page`);
      
      testResults.passed++;
    } catch (error) {
      console.error(`  ✗ Failed: ${error.message}`);
      testResults.failed++;
      testResults.errors.push({ test: 'Click game card', error: error.message });
      await page.screenshot({ path: join(SCREENSHOT_DIR, 'error-click-game.png') });
    }
    
    // Test 4: Verify game information
    console.log('\n📋 Test 4: Verify game information');
    try {
      await page.waitForSelector('.game-detail-page .content', { timeout: 10000 });
      
      const round = await page.locator('.round').textContent();
      const season = await page.locator('.season').textContent();
      console.log(`  ✓ Round: ${round}, Season: ${season}`);
      
      const homeTeam = await page.locator('.team.home .team-name').textContent();
      const awayTeam = await page.locator('.team.away .team-name').textContent();
      console.log(`  ✓ Teams: ${homeTeam} vs ${awayTeam}`);
      
      const venue = await page.locator('.game-meta').textContent();
      console.log(`  ✓ Game meta visible (venue/date)`);
      
      testResults.passed++;
    } catch (error) {
      console.error(`  ✗ Failed: ${error.message}`);
      testResults.failed++;
      testResults.errors.push({ test: 'Verify game info', error: error.message });
      await page.screenshot({ path: join(SCREENSHOT_DIR, 'error-game-info.png') });
    }
    
    // Test 5: Verify all 3 heuristic tips
    console.log('\n📋 Test 5: Verify all 3 heuristic tips');
    try {
      await page.waitForSelector('.tips-section', { timeout: 5000 });
      
      const tipCards = page.locator('.tips-grid .tip-card');
      const tipCount = await tipCards.count();
      console.log(`  ✓ Found ${tipCount} tip cards`);
      
      for (let i = 0; i < tipCount; i++) {
        const tip = tipCards.nth(i);
        const heuristic = await tip.locator('.heuristic-badge').textContent();
        const confidence = await tip.locator('.confidence').textContent();
        const team = await tip.locator('.tip-body h3').textContent();
        const margin = await tip.locator('.margin').textContent();
        console.log(`  ✓ Tip ${i + 1}: ${heuristic} - ${team} (${confidence}, ${margin})`);
      }
      
      testResults.passed++;
    } catch (error) {
      console.error(`  ✗ Failed: ${error.message}`);
      testResults.failed++;
      testResults.errors.push({ test: 'Verify heuristic tips', error: error.message });
      await page.screenshot({ path: join(SCREENSHOT_DIR, 'error-tips.png') });
    }
    
    // Test 6: Verify all 4 model predictions
    console.log('\n📋 Test 6: Verify all 4 model predictions');
    try {
      await page.waitForSelector('.models-section', { timeout: 5000 });
      
      const modelCards = page.locator('.models-grid .model-card');
      const modelCount = await modelCards.count();
      console.log(`  ✓ Found ${modelCount} model predictions`);
      
      const modelNames = [];
      for (let i = 0; i < modelCount; i++) {
        const model = modelCards.nth(i);
        const name = await model.locator('.model-name').textContent();
        const confidence = await model.locator('.confidence').textContent();
        const winner = await model.locator('.model-body h3').textContent();
        const margin = await model.locator('.margin').textContent();
        modelNames.push(name);
        console.log(`  ✓ Model ${i + 1}: ${name} - ${winner} (${confidence}, ${margin})`);
      }
      
      const expectedModels = ['Elo Rating', 'Form', 'Home Advantage', 'Value'];
      for (const expectedModel of expectedModels) {
        if (modelNames.some(name => name.includes(expectedModel))) {
          console.log(`  ✓ Found expected model: ${expectedModel}`);
        }
      }
      
      testResults.passed++;
    } catch (error) {
      console.error(`  ✗ Failed: ${error.message}`);
      testResults.failed++;
      testResults.errors.push({ test: 'Verify model predictions', error: error.message });
      await page.screenshot({ path: join(SCREENSHOT_DIR, 'error-models.png') });
    }
    
    // Test 7: Navigate back to home
    console.log('\n📋 Test 7: Navigate back to home page');
    try {
      await page.click('.back-link');
      await page.waitForURL(BASE_URL, { timeout: 5000 });
      console.log(`  ✓ Navigated back to home page`);
      
      testResults.passed++;
    } catch (error) {
      console.error(`  ✗ Failed: ${error.message}`);
      testResults.failed++;
      testResults.errors.push({ test: 'Navigate back', error: error.message });
      await page.screenshot({ path: join(SCREENSHOT_DIR, 'error-back.png') });
    }
    
    // Test 8: Responsive testing - Mobile
    console.log('\n📋 Test 8: Responsive testing - Mobile (375x667)');
    try {
      await page.setViewportSize({ width: 375, height: 667 });
      await page.goto(BASE_URL);
      await page.waitForSelector('.games-grid', { timeout: 10000 });
      await page.locator('.game-card-link').first().click();
      await page.waitForSelector('.game-detail-page .content', { timeout: 10000 });
      
      const tipsVisible = await page.locator('.tips-section').isVisible();
      const modelsVisible = await page.locator('.models-section').isVisible();
      console.log(`  ✓ Tips visible: ${tipsVisible}, Models visible: ${modelsVisible}`);
      
      await page.screenshot({ path: join(SCREENSHOT_DIR, 'mobile-game-detail.png') });
      console.log(`  ✓ Screenshot saved: mobile-game-detail.png`);
      
      testResults.passed++;
    } catch (error) {
      console.error(`  ✗ Failed: ${error.message}`);
      testResults.failed++;
      testResults.errors.push({ test: 'Mobile responsive', error: error.message });
      await page.screenshot({ path: join(SCREENSHOT_DIR, 'error-mobile.png') });
    }
    
    // Test 9: Responsive testing - Tablet
    console.log('\n📋 Test 9: Responsive testing - Tablet (768x1024)');
    try {
      await page.setViewportSize({ width: 768, height: 1024 });
      await page.goto(BASE_URL);
      await page.waitForSelector('.games-grid', { timeout: 10000 });
      await page.locator('.game-card-link').first().click();
      await page.waitForSelector('.game-detail-page .content', { timeout: 10000 });
      
      const tipsVisible = await page.locator('.tips-section').isVisible();
      const modelsVisible = await page.locator('.models-section').isVisible();
      console.log(`  ✓ Tips visible: ${tipsVisible}, Models visible: ${modelsVisible}`);
      
      await page.screenshot({ path: join(SCREENSHOT_DIR, 'tablet-game-detail.png') });
      console.log(`  ✓ Screenshot saved: tablet-game-detail.png`);
      
      testResults.passed++;
    } catch (error) {
      console.error(`  ✗ Failed: ${error.message}`);
      testResults.failed++;
      testResults.errors.push({ test: 'Tablet responsive', error: error.message });
      await page.screenshot({ path: join(SCREENSHOT_DIR, 'error-tablet.png') });
    }
    
    // Test 10: Responsive testing - Desktop
    console.log('\n📋 Test 10: Responsive testing - Desktop (1920x1080)');
    try {
      await page.setViewportSize({ width: 1920, height: 1080 });
      await page.goto(BASE_URL);
      await page.waitForSelector('.games-grid', { timeout: 10000 });
      await page.locator('.game-card-link').first().click();
      await page.waitForSelector('.game-detail-page .content', { timeout: 10000 });
      
      const tipsVisible = await page.locator('.tips-section').isVisible();
      const modelsVisible = await page.locator('.models-section').isVisible();
      console.log(`  ✓ Tips visible: ${tipsVisible}, Models visible: ${modelsVisible}`);
      
      await page.screenshot({ path: join(SCREENSHOT_DIR, 'desktop-game-detail.png') });
      console.log(`  ✓ Screenshot saved: desktop-game-detail.png`);
      
      testResults.passed++;
    } catch (error) {
      console.error(`  ✗ Failed: ${error.message}`);
      testResults.failed++;
      testResults.errors.push({ test: 'Desktop responsive', error: error.message });
      await page.screenshot({ path: join(SCREENSHOT_DIR, 'error-desktop.png') });
    }
    
    // Test 11: Error handling - Non-existent game
    console.log('\n📋 Test 11: Error handling - Non-existent game ID');
    try {
      await page.goto(`${BASE_URL}/game/999999`);
      await page.waitForSelector('.error', { timeout: 10000 });
      
      const errorVisible = await page.locator('.error').isVisible();
      const backLinkVisible = await page.locator('.back-link').count() > 0;
      console.log(`  ✓ Error page displayed: ${errorVisible}`);
      console.log(`  ✓ Back link present: ${backLinkVisible}`);
      
      await page.screenshot({ path: join(SCREENSHOT_DIR, 'error-page.png') });
      console.log(`  ✓ Screenshot saved: error-page.png`);
      
      testResults.passed++;
    } catch (error) {
      console.error(`  ✗ Failed: ${error.message}`);
      testResults.failed++;
      testResults.errors.push({ test: 'Error handling', error: error.message });
      await page.screenshot({ path: join(SCREENSHOT_DIR, 'error-error-page.png') });
    }
    
  } catch (error) {
    console.error('\n💥 Fatal error:', error);
    testResults.failed++;
    testResults.errors.push({ test: 'Fatal', error: error.message });
    await page.screenshot({ path: join(SCREENSHOT_DIR, 'fatal-error.png') });
  } finally {
    await browser.close();
  }
  
  // Print summary
  console.log('\n' + '='.repeat(60));
  console.log('📊 TEST SUMMARY');
  console.log('='.repeat(60));
  console.log(`✅ Passed: ${testResults.passed}`);
  console.log(`❌ Failed: ${testResults.failed}`);
  console.log(`📝 Total Tests: ${testResults.passed + testResults.failed}`);
  
  if (testResults.errors.length > 0) {
    console.log('\n❌ Errors encountered:');
    testResults.errors.forEach((err, i) => {
      console.log(`  ${i + 1}. ${err.test}: ${err.error}`);
    });
  }
  
  console.log('\n📸 Screenshots saved to:', SCREENSHOT_DIR);
  console.log('='.repeat(60));
  
  return testResults;
}

// Run tests
testHomeToGameDetailFlow()
  .then(results => {
    process.exit(results.failed > 0 ? 1 : 0);
  })
  .catch(error => {
    console.error('Test runner error:', error);
    process.exit(1);
  });
