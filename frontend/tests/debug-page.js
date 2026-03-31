import { chromium } from 'playwright';

async function debugPage() {
  console.log('🔍 Debugging page structure...\n');
  
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext();
  const page = await context.newPage();
  
  try {
    // Navigate to home page
    console.log('1. Navigating to home page...');
    await page.goto('http://localhost:3000', { waitUntil: 'networkidle' });
    console.log('   Page loaded');
    
    // Wait a bit more
    await page.waitForTimeout(3000);
    
    // Get page title
    const title = await page.title();
    console.log(`   Title: ${title}`);
    
    // Get page content
    const content = await page.content();
    console.log(`   Content length: ${content.length}`);
    
    // Check for hero section
    const heroExists = await page.locator('.hero').count();
    console.log(`   .hero elements: ${heroExists}`);
    
    const h1Exists = await page.locator('h1').count();
    console.log(`   h1 elements: ${h1Exists}`);
    
    // Get all h1 text
    const h1Texts = await page.locator('h1').allTextContents();
    console.log(`   h1 texts: ${JSON.stringify(h1Texts)}`);
    
    // Check for games grid
    const gamesGridExists = await page.locator('.games-grid').count();
    console.log(`   .games-grid elements: ${gamesGridExists}`);
    
    // Check for game cards
    const gameCardExists = await page.locator('.game-card').count();
    console.log(`   .game-card elements: ${gameCardExists}`);
    
    // Check for game card links
    const gameCardLinkExists = await page.locator('a').count();
    console.log(`   Total links: ${gameCardLinkExists}`);
    
    // Get first few links
    const links = await page.locator('a').all();
    console.log(`   First 5 links:`);
    for (let i = 0; i < Math.min(5, links.length); i++) {
      const href = await links[i].getAttribute('href');
      const text = await links[i].textContent();
      console.log(`     ${i + 1}. href="${href}", text="${text?.trim()}"`);
    }
    
    // Take screenshot
    await page.screenshot({ path: 'debug-home.png', fullPage: true });
    console.log('\n   Screenshot saved: debug-home.png');
    
    // Try to navigate to a game detail page directly
    console.log('\n2. Navigating to game detail page...');
    await page.goto('http://localhost:3000/game/245', { waitUntil: 'networkidle' });
    await page.waitForTimeout(3000);
    
    const gameTitle = await page.title();
    console.log(`   Title: ${gameTitle}`);
    
    // Check for game detail page elements
    const gameDetailExists = await page.locator('.game-detail-page').count();
    console.log(`   .game-detail-page elements: ${gameDetailExists}`);
    
    const contentExists = await page.locator('.content').count();
    console.log(`   .content elements: ${contentExists}`);
    
    const tipsSectionExists = await page.locator('.tips-section').count();
    console.log(`   .tips-section elements: ${tipsSectionExists}`);
    
    const modelsSectionExists = await page.locator('.models-section').count();
    console.log(`   .models-section elements: ${modelsSectionExists}`);
    
    const backLinkExists = await page.locator('.back-link').count();
    console.log(`   .back-link elements: ${backLinkExists}`);
    
    // Get all sections
    const sections = await page.locator('section').all();
    console.log(`   Total sections: ${sections.length}`);
    
    // Take screenshot
    await page.screenshot({ path: 'debug-game-detail.png', fullPage: true });
    console.log('\n   Screenshot saved: debug-game-detail.png');
    
    // Get page content for game detail
    const gameContent = await page.content();
    console.log(`   Content length: ${gameContent.length}`);
    
  } catch (error) {
    console.error('Error:', error);
    await page.screenshot({ path: 'debug-error.png' });
  } finally {
    await browser.close();
  }
}

debugPage();
