# Playwright E2E Tests

This directory contains end-to-end tests for the WhatIsMyTip application using Playwright.

## Test Files

### Main Test Suite
- **`game-detail-flow.spec.ts`** - Comprehensive Playwright test suite covering:
  - Home page navigation and game card display
  - Game card click navigation to detail page
  - Game detail page content verification
  - Heuristic tips display (Best Bet, YOLO, High Risk High Reward)
  - Model predictions display (Elo Rating, Form, Home Advantage, Value)
  - Responsive testing (mobile, tablet, desktop)
  - Navigation back to home page
  - Error handling for non-existent game IDs

### Test Runners
- **`run-tests.js`** - Standalone Node.js test runner with detailed output
- **`debug-page.js`** - Simple page structure debugger
- **`debug-detailed.js`** - Detailed page analyzer with state checking

### Test Reports
- **`TEST_REPORT.md`** - Detailed test execution report with findings

## Prerequisites

1. Install dependencies:
   ```bash
   cd frontend
   bun install
   ```

2. Install Playwright browsers:
   ```bash
   bunx playwright install chromium
   ```

3. Ensure servers are running:
   - Frontend: http://localhost:3000
   - Backend: http://localhost:8000

## Running Tests

### Using Playwright Test Runner
```bash
cd frontend
bunx playwright test
```

### Headed Mode (with browser window)
```bash
cd frontend
bunx playwright test --headed
```

### Using Standalone Runner
```bash
cd frontend
node tests/run-tests.js
```

### Debug Mode
```bash
cd frontend
node tests/debug-page.js
```

### Detailed Debug
```bash
cd frontend
node tests/debug-detailed.js
```

## Test Screenshots

Screenshots are saved to `frontend/test-screenshots/` directory:
- `mobile-game-detail.png` - Mobile viewport screenshot
- `tablet-game-detail.png` - Tablet viewport screenshot
- `desktop-game-detail.png` - Desktop viewport screenshot
- `error-*.png` - Error screenshots for failed tests

## Test Coverage

### Home Page Tests
- ✅ Page loads successfully
- ✅ Hero section is visible
- ❌ Game cards are displayed (API integration issue)
- ❌ Tips are shown on game cards (API integration issue)

### Game Detail Page Tests
- ✅ Page loads successfully
- ✅ Back link is present
- ❌ Game information is displayed (API integration issue)
- ❌ Heuristic tips are shown (API integration issue)
- ❌ Model predictions are shown (API integration issue)

### Responsive Tests
- ❌ Mobile viewport (375x667) (content not rendering)
- ❌ Tablet viewport (768x1024) (content not rendering)
- ❌ Desktop viewport (1920x1080) (content not rendering)

### Navigation Tests
- ❌ Click game card navigation (no cards available)
- ❌ Back to home navigation (game detail not loading)

### Error Handling Tests
- ❌ Non-existent game ID (error state not properly displayed)

## Known Issues

### API Integration Failure
The tests revealed that the frontend is not successfully fetching data from the backend API. This is causing most tests to fail because:

1. **Home Page**: No game cards are displayed because `/api/games` endpoint is not returning data or timing out
2. **Game Detail Page**: Content sections are not rendered because `/api/games/{id}/detail` endpoint is not returning data or timing out

### Recommended Fixes
1. Verify backend API is running and accessible
2. Check CORS configuration
3. Add proper error handling for API failures
4. Improve loading state visibility
5. Add timeout handling for slow API responses

## Writing New Tests

When adding new tests:

1. Use descriptive test names
2. Add test steps as `test.step()` for better reporting
3. Use explicit waits with `page.waitForSelector()`
4. Add assertions with `expect()`
5. Include error handling with try/catch
6. Capture screenshots on failure

### Example Test
```typescript
test('should display game cards', async ({ page }) => {
  await page.goto('http://localhost:3000');
  await page.waitForSelector('.games-grid', { timeout: 10000 });
  
  const gameCards = page.locator('.game-card');
  const count = await gameCards.count();
  expect(count).toBeGreaterThan(0);
});
```

## CI/CD Integration

To add these tests to your CI/CD pipeline:

```yaml
# .github/workflows/e2e-tests.yml
name: E2E Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
        with:
          node-version: '18'
      - run: cd frontend && bun install
      - run: bunx playwright install --with-deps
      - run: bunx playwright test
      - uses: actions/upload-artifact@v3
        if: always()
        with:
          name: playwright-report
          path: frontend/playwright-report/
```

## Resources

- [Playwright Documentation](https://playwright.dev/)
- [Playwright Test Documentation](https://playwright.dev/docs/intro)
- [Best Practices](https://playwright.dev/docs/best-practices)
