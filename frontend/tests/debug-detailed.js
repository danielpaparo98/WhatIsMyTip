import { chromium } from 'playwright';

async function debugDetailed() {
  console.log('🔍 Detailed page debugging...\n');
  
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext();
  const page = await context.newPage();
  
  try {
    // Navigate to home page
    console.log('1. Home Page Analysis');
    console.log('   Navigating to home page...');
    await page.goto('http://localhost:3000', { waitUntil: 'networkidle' });
    
    // Wait for content to potentially load
    await page.waitForTimeout(5000);
    
    // Check for various states
    const loadingExists = await page.locator('.loading').count();
    const errorExists = await page.locator('.error').count();
    const emptyExists = await page.locator('.empty').count();
    const gamesGridExists = await page.locator('.games-grid').count();
    
    console.log(`   .loading elements: ${loadingExists}`);
    console.log(`   .error elements: ${errorExists}`);
    console.log(`   .empty elements: ${emptyExists}`);
    console.log(`   .games-grid elements: ${gamesGridExists}`);
    
    if (loadingExists > 0) {
      const loadingText = await page.locator('.loading').textContent();
      console.log(`   Loading text: "${loadingText}"`);
    }
    
    if (errorExists > 0) {
      const errorText = await page.locator('.error').textContent();
      console.log(`   Error text: "${errorText}"`);
    }
    
    if (emptyExists > 0) {
      const emptyText = await page.locator('.empty').textContent();
      console.log(`   Empty text: "${emptyText}"`);
    }
    
    // Get all divs with class containing 'game'
    const gameDivs = await page.locator('div[class*="game"]').all();
    console.log(`   Divs with 'game' in class: ${gameDivs.length}`);
    
    // Get all text on page
    const bodyText = await page.locator('body').textContent();
    console.log(`   Body text length: ${bodyText?.length}`);
    
    await page.screenshot({ path: 'debug-detailed-home.png', fullPage: true });
    console.log('   Screenshot saved: debug-detailed-home.png\n');
    
    // Navigate to game detail page
    console.log('2. Game Detail Page Analysis');
    console.log('   Navigating to game detail page...');
    await page.goto('http://localhost:3000/game/245', { waitUntil: 'networkidle' });
    
    // Wait for content to potentially load
    await page.waitForTimeout(5000);
    
    // Check for various states
    const gameLoadingExists = await page.locator('.loading').count();
    const gameErrorExists = await page.locator('.error').count();
    const contentExists = await page.locator('.content').count();
    const gameDetailPageExists = await page.locator('.game-detail-page').count();
    
    console.log(`   .loading elements: ${gameLoadingExists}`);
    console.log(`   .error elements: ${gameErrorExists}`);
    console.log(`   .content elements: ${contentExists}`);
    console.log(`   .game-detail-page elements: ${gameDetailPageExists}`);
    
    if (gameLoadingExists > 0) {
      const loadingText = await page.locator('.loading').textContent();
      console.log(`   Loading text: "${loadingText}"`);
    }
    
    if (gameErrorExists > 0) {
      const errorText = await page.locator('.error h2').textContent();
      const errorMsg = await page.locator('.error p').textContent();
      console.log(`   Error title: "${errorText}"`);
      console.log(`   Error message: "${errorMsg}"`);
    }
    
    // Check for back link
    const backLinkExists = await page.locator('.back-link').count();
    console.log(`   .back-link elements: ${backLinkExists}`);
    
    if (backLinkExists > 0) {
      const backLinkText = await page.locator('.back-link').textContent();
      console.log(`   Back link text: "${backLinkText}"`);
    }
    
    // Get all sections
    const sections = await page.locator('section').all();
    console.log(`   Total sections: ${sections.length}`);
    
    // Get all divs
    const allDivs = await page.locator('div').all();
    console.log(`   Total divs: ${allDivs.length}`);
    
    // Get all text on page
    const gameBodyText = await page.locator('body').textContent();
    console.log(`   Body text length: ${gameBodyText?.length}`);
    console.log(`   First 500 chars: "${gameBodyText?.substring(0, 500)}"`);
    
    await page.screenshot({ path: 'debug-detailed-game-detail.png', fullPage: true });
    console.log('   Screenshot saved: debug-detailed-game-detail.png\n');
    
    // Check console logs
    console.log('3. Console Logs');
    const logs = [];
    page.on('console', msg => {
      logs.push({ type: msg.type(), text: msg.text() });
    });
    
    // Reload page to capture logs
    await page.reload({ waitUntil: 'networkidle' });
    await page.waitForTimeout(5000);
    
    console.log(`   Console logs captured: ${logs.length}`);
    logs.forEach((log, i) => {
      console.log(`   [${log.type}] ${log.text}`);
    });
    
  } catch (error) {
    console.error('Error:', error);
    await page.screenshot({ path: 'debug-detailed-error.png' });
  } finally {
    await browser.close();
  }
}

debugDetailed();
