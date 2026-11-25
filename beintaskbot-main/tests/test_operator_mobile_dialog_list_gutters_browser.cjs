/* Mobile dialog-list edge-to-edge regression. Local fixtures only; no account or API writes. */
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const repo = path.resolve(__dirname, '..');
const docs = path.join(repo, 'docs');
const html = fs.readFileSync(path.join(docs, 'tenant-app.html'), 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');
const route = {
  provider: 'connect', lead_id: 41, channel_id: 'channel-mobile', conversation_id: 'mobile-contact',
  customer_kind: 'contact', customer_name: 'Example customer with a deliberately long display name', channel_name: 'Texniki Dəstək', customer_phone: '994500000001', agent_id: 'agent-a', agent_name: 'Agent',
  agent_status: 'manual', can_send: true, controls_allowed: true, available_actions: []
};
const messages = [{
  id: 'message-1', external_id: 'external-1', direction: 'incoming', message_type: 'text',
  body: 'A long but readable mobile conversation message.', author_type: 'customer',
  happened_at: '2026-11-01T12:00:00Z'
}];
const profile = { member: { tenant_id: 'tenant-a', telegram_id: 11, role: 'worker', permissions: ['dialogs', 'support'], active: true }, capabilities: { modules: { dialogs: true } } };
const envelope = result => ({ ok: true, status: 200, json: async () => ({ success: true, result }) });

const server = http.createServer((req, res) => {
  const send = (status, type, body) => { res.writeHead(status, { 'content-type': type }); res.end(body); };
  if (req.url.startsWith('/app')) return send(200, 'text/html; charset=utf-8', html);
  if (req.url.startsWith('/assets/')) {
    const filename = path.join(docs, path.basename(req.url.split('?')[0]));
    return fs.existsSync(filename) ? send(200, filename.endsWith('.css') ? 'text/css' : 'text/javascript', fs.readFileSync(filename)) : send(404, 'text/plain', 'missing asset');
  }
  return send(404, 'text/plain', 'not found');
});

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, ignoreDefaultArgs: ['--hide-scrollbars'], args: ['--no-sandbox'] });
  try {
    for (const width of [320, 360, 390, 430, 1440]) {
      const context = await browser.newContext({ viewport: { width, height: 844 }, isMobile: width <= 430, hasTouch: width <= 430 });
      const page = await context.newPage();
      await page.route('**/*', request => new URL(request.request().url()).origin === origin ? request.continue() : request.abort());
      await page.goto(`${origin}/app?view=dialogs`, { waitUntil: 'domcontentloaded' });
      await page.addScriptTag({ path: path.join(docs, 'tenant-operator-i18n.js') });
      await page.evaluate(({ route, messages, profile }) => {
        document.querySelectorAll('.view').forEach(node => node.classList.toggle('active', node.id === 'view-dialogs'));
        window.alert = () => {};
        window.tenantProfileSnapshot = profile;
        window.testRoute = route;
        window.fetch = async (target, options = {}) => {
          const url = String(target);
          if (url.includes('/api/platform/me')) return { ok: true, status: 200, json: async () => profile };
          if (url.includes('/dialogs/new?')) {
            if (options.method !== 'GET') throw new Error('Opening new chat must not send a message');
            return { ok: true, status: 200, json: async () => ({
              success: true, result: { sources: [{ channel_id: route.channel_id, name: route.channel_name }] }
            }) };
          }
          if (url.includes('/dialogs/history')) return { ok: true, status: 200, json: async () => ({ success: true, result: { dialog: route, messages, next_before: null } }) };
          if (url.includes('/dialogs?')) return { ok: true, status: 200, json: async () => ({ success: true, result: { dialogs: [{ ...route, last_message_id: 'message-1', last_message_direction: 'incoming', last_message_text: messages[0].body, unread_count: 1 }], channels: [], agents: [], next_offset: null } }) };
          return { ok: true, status: 200, json: async () => ({ success: true, result: {} }) };
        };
      }, { route, messages, profile });
      await page.addScriptTag({ path: path.join(docs, 'tenant-chat-send.js') });
      await page.addScriptTag({ path: path.join(docs, 'tenant-operator-dialogs.js') });
      // Include the production consultation module: its menu entry must not change header layout.
      await page.addScriptTag({ path: path.join(docs, 'tenant-dialog-consultations.js') });
      await page.evaluate(() => window.tenantOperator.refresh());
      await page.locator('.operator-row').waitFor();
      await page.evaluate(() => { const nav = document.querySelector('#nativeCrmNav'); if (nav) nav.open = false; });
      await page.addScriptTag({ path: path.join(docs, 'tenant-dialog-new-chat.js') });
      const newChat = page.locator('#operatorNewChat');
      const search = page.locator('#operatorSearch');
      await newChat.waitFor();
      assert.equal(await newChat.textContent(), '+');
      assert.match(await newChat.getAttribute('aria-label'), /^(Yeni çat|Новый чат|New chat)$/);
      const plusBox = await newChat.boundingBox(), searchBox = await search.boundingBox();
      assert.ok(plusBox.width >= 44 && plusBox.height >= 44);
      assert.ok(plusBox.x >= searchBox.x && Math.abs(plusBox.x + plusBox.width - searchBox.x - searchBox.width) <= 1);
      assert.ok(Math.abs(plusBox.y - searchBox.y) <= 1 && plusBox.y + plusBox.height <= searchBox.y + searchBox.height + 1);
      assert.ok(await search.evaluate(input => parseFloat(getComputedStyle(input).paddingInlineEnd)) >= plusBox.width);
      await search.fill('Example');
      await (width <= 430 ? newChat.tap() : newChat.press('Enter'));
      const newChatDialog = page.locator('.operator-new-chat-dialog');
      await newChatDialog.waitFor();
      assert.equal(await newChatDialog.locator('select').inputValue(), route.channel_id);
      await newChatDialog.locator('[data-new-chat-cancel]').click();
      await newChatDialog.waitFor({ state: 'hidden' });
      assert.equal(await search.inputValue(), 'Example', 'opening new chat preserves the search');
      await search.fill('');

      const listGeometry = await page.evaluate(() => {
        const view = document.querySelector('#view-dialogs');
        const pane = document.querySelector('.operator-list-pane');
        const row = document.querySelector('.operator-row');
        const main = document.querySelector('main');
        return {
          viewport: innerWidth,
          documentWidth: document.documentElement.scrollWidth,
          main: { left: main.getBoundingClientRect().left, width: main.getBoundingClientRect().width, paddingLeft: getComputedStyle(main).paddingLeft, paddingRight: getComputedStyle(main).paddingRight },
          view: { left: view.getBoundingClientRect().left, width: view.getBoundingClientRect().width },
          pane: { left: pane.getBoundingClientRect().left, width: pane.getBoundingClientRect().width, paddingLeft: getComputedStyle(pane).paddingLeft, paddingRight: getComputedStyle(pane).paddingRight, radius: getComputedStyle(pane).borderRadius },
          row: { left: row.getBoundingClientRect().left, width: row.getBoundingClientRect().width },
          safeAreaRules: [...document.head.querySelectorAll('style')].map(style => style.textContent).join(' ').includes('env(safe-area-inset-left)') && [...document.head.querySelectorAll('style')].map(style => style.textContent).join(' ').includes('env(safe-area-inset-right)')
        };
      });
      if (width <= 430) {
      assert.ok(Math.abs(listGeometry.pane.left) <= 1, `dialog list should align to viewport edge at ${width}px: ${JSON.stringify(listGeometry)}`);
      assert.ok(listGeometry.pane.width >= width - 1, `dialog list should fill viewport at ${width}px: ${JSON.stringify(listGeometry)}`);
      assert.ok(listGeometry.documentWidth <= width + 1, `dialog list should not create horizontal overflow at ${width}px: ${JSON.stringify(listGeometry)}`);
      assert.ok(listGeometry.row.left > listGeometry.pane.left, 'row controls retain useful internal inset');
      assert.ok(parseFloat(listGeometry.pane.paddingLeft) >= 6 && parseFloat(listGeometry.pane.paddingRight) >= 6, 'dialog list keeps its normal internal padding');
      assert.equal(listGeometry.safeAreaRules, true, 'list content padding accounts for left and right safe areas');
      }
      // Both native scrollbar and overlay-scrollbar platforms must reclaim the
      // short-list width. Never alter pane padding or the chat-history gutter.
      for (const theme of ['light', 'dark']) {
        await page.emulateMedia({ colorScheme: theme });
        await page.evaluate(theme => { document.documentElement.dataset.appTheme = theme; }, theme);
        const geometry = () => page.evaluate(() => {
          const list = document.querySelector('#operatorRows'), pane = list.closest('.operator-list-pane');
          const style = getComputedStyle(list), rect = list.getBoundingClientRect();
          const row = list.querySelector('.operator-row').getBoundingClientRect();
          return { gutter: list.offsetWidth - list.clientWidth - parseFloat(style.borderLeftWidth) - parseFloat(style.borderRightWidth),
            policy: style.scrollbarGutter, overflow: list.scrollHeight > list.clientHeight,
            rowWidth: row.width, contentWidth: list.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
            width: rect.width, padding: [getComputedStyle(pane).paddingLeft, getComputedStyle(pane).paddingRight],
            historyGutter: [...document.querySelectorAll('style')].some(node => node.textContent.includes('.operator-chat-history{overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable}')) };
        });
        const short = await geometry();
        assert.equal(short.historyGutter, true, 'chat history keeps its stable gutter rule');
        if (width === 1440) assert.deepEqual(short.padding, ['10px', '10px'], 'desktop retains original 10px pane insets');
        assert.equal(short.policy, 'auto', `list must not reserve a stable gutter: ${width}/${theme}`);
        assert.equal(short.overflow, false, 'one-row fixture does not overflow');
        assert.ok(Math.abs(short.gutter) <= 1, `short list has no empty scrollbar strip: ${JSON.stringify(short)}`);
        assert.ok(Math.abs(short.rowWidth - short.contentWidth) <= 1, 'short row fills available list content width');
        await page.evaluate(() => { const list = document.querySelector('#operatorRows'), row = list.firstElementChild; for (let i = 0; i < 60; i++) list.append(row.cloneNode(true)); });
        const long = await geometry();
        assert.equal(long.overflow, true, 'long list still scrolls');
        assert.deepEqual(long.padding, short.padding, 'overflow cannot change pane padding');
        assert.equal(long.historyGutter, short.historyGutter, 'chat history policy is unchanged');
        assert.ok(await page.evaluate(() => { const list = document.querySelector('#operatorRows'); list.scrollTop = 150; return list.scrollTop > 0; }), 'long list scroll position advances');
        await page.evaluate(() => { const list = document.querySelector('#operatorRows'); while (list.children.length > 1) list.lastElementChild.remove(); });
        const restored = await geometry();
        assert.equal(restored.overflow, false, 'filtering back to one row removes overflow');
        assert.ok(Math.abs(restored.gutter) <= 1, 'filtering releases the scrollbar strip');
        assert.ok(Math.abs(restored.rowWidth - short.rowWidth) <= 1, 'short-list row width restores');
        assert.deepEqual(restored.padding, short.padding, 'filtering preserves internal padding');
        assert.equal(restored.historyGutter, short.historyGutter, 'filtering never changes chat history');
        console.log(`PASS dialog list scrollbar ${width}px ${theme}: short/overflow/short`);
      }
      if (process.env.SCREENSHOT_DIR) {
        fs.mkdirSync(process.env.SCREENSHOT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.SCREENSHOT_DIR, `dialog-list-${width}.png`), fullPage: false });
      }

      if (width === 320) {
        await page.locator('.operator-row').focus();
        await page.keyboard.press('Enter');
        await page.waitForFunction(() => document.querySelector('#operatorWorkspace')?.dataset.selected === 'true');
        await page.locator('[data-operator-back]').click();
        await page.waitForFunction(() => document.querySelector('#operatorWorkspace')?.dataset.selected === 'false');
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'keyboard open/back keeps the mobile list within the viewport');
      }

      if (width <= 430) {
        await page.locator('.operator-row').click();
        await page.locator('#operatorConversationPane').waitFor();
        await page.waitForTimeout(200);
        const chatGeometry = await page.evaluate(() => {
          const pane = document.querySelector('#operatorConversationPane').getBoundingClientRect();
          const header = document.querySelector('#operatorConversationHeader').getBoundingClientRect();
          const composer = document.querySelector('.operator-composer').getBoundingClientRect();
          return { viewport: innerWidth, documentWidth: document.documentElement.scrollWidth, pane: { left: pane.left, right: pane.right, width: pane.width }, header: { left: header.left, right: header.right, height: header.height }, composer: { left: composer.left, right: composer.right } };
        });
        assert.ok(Math.abs(chatGeometry.pane.left) <= 1 && chatGeometry.pane.width >= width - 1, `opened chat remains full-width at ${width}px: ${JSON.stringify(chatGeometry)}`);
        assert.ok(chatGeometry.documentWidth <= width + 1, 'opening chat does not introduce horizontal overflow');
        assert.ok(chatGeometry.header.height <= 66, 'mobile header stays one compact toolbar: ' + JSON.stringify(chatGeometry));
        const title = await page.locator('#operatorDialogTitleSlot').boundingBox();
        const action = await page.locator('[data-operator-create-ticket]').boundingBox();
        assert.ok(action && action.height >= 44 && action.y < chatGeometry.header.height, 'create task keeps a 44px inline touch target');
        const avatar = await page.locator('#operatorClientCardOpen').boundingBox();
        assert.ok(avatar && avatar.height >= 44, 'avatar touch target is preserved');
        assert.ok(Math.abs(action.y - avatar.y) <= 16, 'task stays beside avatar, not on a second row');
        const more = await page.locator('#operatorMoreActions summary').boundingBox();
        assert.ok(Math.abs(more.y - avatar.y) <= 16, 'menu stays beside avatar, not on a second row');
        assert.ok(more.y < chatGeometry.header.height, 'more menu remains in the header row');
        assert.ok(title.width > 60, 'customer title retains useful space');
        await page.locator('#operatorMoreActions summary').click();
        assert.ok(await page.locator('.operator-action-menu').isVisible(), 'overflow actions still open');
        assert.ok(await page.locator('#dialogDeveloperAsk').isVisible(), 'real consultation entry is present');
        await page.locator('#operatorMoreActions summary').click();

        if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.SCREENSHOT_DIR, 'dialog-open-chat-' + width + '.png'), fullPage: false });
        await page.goBack();
        await page.waitForFunction(() => document.querySelector('#operatorWorkspace')?.dataset.selected === 'false');
        await page.locator('.operator-row').click();
        await page.locator('[data-operator-back]').click();
        await page.waitForFunction(() => document.querySelector('#operatorWorkspace')?.dataset.selected === 'false');
      }
      await context.close();
      console.log(`PASS mobile dialog list ${width}px`);
    }

    const standaloneContext = await chromium.launchPersistentContext(`/tmp/operator-dialog-standalone-${process.pid}`, {
      headless: true,
      executablePath: process.env.CHROMIUM_PATH || undefined,
      args: ['--no-sandbox', `--app=${origin}/app?view=dialogs`],
      viewport: { width: 390, height: 844 }
    });
    try {
      const page = standaloneContext.pages()[0] || await standaloneContext.newPage();
      await page.route('**/*', request => new URL(request.request().url()).origin === origin ? request.continue() : request.abort());
      if (!page.url().startsWith(origin)) await page.goto(`${origin}/app?view=dialogs`, { waitUntil: 'domcontentloaded' });
      // Chromium's --app launches the standalone-style window used here, but
      // does not register an installed PWA, so display-mode:standalone is not
      // a reliable signal in this CI fixture. The app has no display-mode CSS;
      // assert the app route and measure its actual app-window geometry below.
      await page.evaluate(({ route, messages, profile }) => {
        document.querySelectorAll('.view').forEach(node => node.classList.toggle('active', node.id === 'view-dialogs'));
        window.alert = () => {};
        window.tenantProfileSnapshot = profile;
        window.fetch = async target => {
          const url = String(target);
          if (url.includes('/api/platform/me')) return { ok: true, status: 200, json: async () => profile };
          if (url.includes('/dialogs/history')) return { ok: true, status: 200, json: async () => ({ success: true, result: { dialog: route, messages, next_before: null } }) };
          if (url.includes('/dialogs?')) return { ok: true, status: 200, json: async () => ({ success: true, result: { dialogs: [{ ...route, last_message_id: 'message-1', last_message_direction: 'incoming', last_message_text: messages[0].body, unread_count: 1 }], channels: [], agents: [], next_offset: null } }) };
          return { ok: true, status: 200, json: async () => ({ success: true, result: {} }) };
        };
      }, { route, messages, profile });
      await page.addScriptTag({ path: path.join(docs, 'tenant-operator-i18n.js') });
      await page.addScriptTag({ path: path.join(docs, 'tenant-chat-send.js') });
      await page.addScriptTag({ path: path.join(docs, 'tenant-operator-dialogs.js') });
      // Include the production consultation module: its menu entry must not change header layout.
      await page.addScriptTag({ path: path.join(docs, 'tenant-dialog-consultations.js') });
      await page.evaluate(() => window.tenantOperator.refresh());
      await page.locator('.operator-row').waitFor();
      await page.evaluate(() => { const nav = document.querySelector('#nativeCrmNav'); if (nav) nav.open = false; });
      assert.ok(page.url().startsWith(`${origin}/app`), 'the Chromium app-window surface is displaying the app');
      const standaloneGeometry = await page.evaluate(() => {
        const pane = document.querySelector('.operator-list-pane').getBoundingClientRect();
        const list = document.querySelector('.operator-list-pane');
        return { width: innerWidth, left: pane.left, paneWidth: pane.width, documentWidth: document.documentElement.scrollWidth, paddingLeft: getComputedStyle(list).paddingLeft, paddingRight: getComputedStyle(list).paddingRight };
      });
      assert.ok(Math.abs(standaloneGeometry.left) <= 1 && standaloneGeometry.paneWidth >= 389, `standalone PWA list is edge-to-edge: ${JSON.stringify(standaloneGeometry)}`);
      assert.ok(standaloneGeometry.documentWidth <= standaloneGeometry.width + 1, 'standalone PWA list has no horizontal overflow');
      assert.ok(parseFloat(standaloneGeometry.paddingLeft) >= 6 && parseFloat(standaloneGeometry.paddingRight) >= 6, 'standalone PWA list preserves safe internal spacing');
      if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: path.join(process.env.SCREENSHOT_DIR, 'dialog-list-standalone-390.png'), fullPage: false });
      console.log('PASS mobile PWA app-window dialog list 390px');
    } finally {
      await standaloneContext.close();
    }
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
