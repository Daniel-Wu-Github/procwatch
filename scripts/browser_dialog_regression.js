// Run through Playwright's run-code facility with this file's absolute path.
// Run scripts/browser_fixture.py first and open its URL in a separate browser tab.
// Only use the recording-only fixture: this test confirms fictional processes.
async (page) => {
  let fixtureUrl;
  for (const candidate of page.context().pages()) {
    if ((await candidate.title()).startsWith('TEST FIXTURE ·') && new URL(candidate.url()).hostname === '127.0.0.1') {
      fixtureUrl = candidate.url();
      break;
    }
  }
  if (!fixtureUrl) throw Error('Open the recording-only TEST FIXTURE in a separate tab first');
  const failures = [];
  const observations = {};
  const check = (condition, message) => { if (!condition) failures.push(message); };
  const deferred = () => {
    let resolve;
    const promise = new Promise(r => { resolve = r; });
    return { promise, resolve };
  };
  const preview = pids => ({ pids, refused: [], frees: { mem: 123456, cpu: 1 } });
  const nextPaint = async p => p.evaluate(() => new Promise(resolve => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  }));
  const setup = async p => {
    await p.goto(fixtureUrl);
    await p.getByRole('button', { name: 'Open group Studio', exact: true }).click();
    await p.getByRole('checkbox', { name: 'Select process Renderer 101', exact: true }).check();
    await p.locator('#stop').waitFor({ state: 'visible' });
  };

  // Separate pages ensure each scenario starts with clean UI state.
  const stale = await page.context().newPage();
  try {
    await setup(stale);
    const oldRequest = deferred();
    const latestRequest = deferred();
    let count = 0;
    await stale.route('**/api/preview', route => {
      count += 1;
      (count === 1 ? oldRequest : latestRequest).resolve(route);
    });
    await stale.locator('#stop').click();
    const oldRoute = await oldRequest.promise;
    await stale.locator('#cancel').click();
    await stale.locator('#force').click();
    const latestRoute = await latestRequest.promise;
    const latestResponse = stale.waitForResponse(r => r.url().endsWith('/api/preview'));
    await latestRoute.fulfill({ json: preview([101]) });
    await (await latestResponse).finished();
    await stale.locator('#confirm-action').waitFor({ state: 'visible' });
    await nextPaint(stale);
    const latestText = await stale.locator('#confirm-list').innerText();
    check(latestText.includes('101'), 'Latest Force preview must display PID 101');
    const oldResponse = stale.waitForResponse(r => r.url().endsWith('/api/preview'));
    await oldRoute.fulfill({ json: preview([102]) });
    await (await oldResponse).finished();
    await nextPaint(stale);
    const afterOldText = await stale.locator('#confirm-list').innerText();
    observations.stalePreview = { latestText, afterOldText };
    check(afterOldText === latestText, 'Late canceled Stop preview must not replace the Force target list');
    check(!(await stale.locator('#confirm-title').innerText()).includes('Stop'), 'Force dialog must retain its action title');
    const submission = deferred();
    await stale.route(/\/api\/(stop|force)$/, async route => {
      submission.resolve({ url: route.request().url(), body: route.request().postDataJSON() });
      await route.fulfill({ json: { outcomes: [{pid:101,status:'signalled'}], refused: [] } });
    });
    await stale.locator('#confirm-action').click();
    const submitted = await submission.promise;
    observations.stalePreview.submitted = submitted;
    check(submitted.url.endsWith('/api/force'), 'Confirmation must submit the Force action');
    check(JSON.stringify(submitted.body.pids) === '[101]', 'Force submission must use latest preview PID 101, never stale Stop PID 102');
  } finally {
    await stale.close();
  }

  const pending = await page.context().newPage();
  try {
    await setup(pending);
    await pending.route('**/api/preview', route => route.fulfill({ json: preview([101]) }));
    const sent = deferred();
    let actionCount = 0;
    await pending.route(/\/api\/(stop|force)$/, route => {
      actionCount += 1;
      sent.resolve(route);
    });
    await pending.locator('#stop').click();
    await pending.locator('#confirm-action').click();
    const actionRoute = await sent.promise;
    await nextPaint(pending);
    const disabled = {};
    for (const id of ['stop', 'force', 'confirm-action', 'cancel']) {
      disabled[id] = await pending.locator(`#${id}`).isDisabled();
      check(disabled[id], `${id} must be disabled while a confirmed action is in flight`);
    }
    await pending.keyboard.press('Escape');
    await nextPaint(pending);
    const dialogVisibleAfterEscape = await pending.locator('#confirm').isVisible();
    check(dialogVisibleAfterEscape, 'Escape must not make an in-flight confirmed action appear canceled');
    if (await pending.locator('#cancel').isVisible() && !disabled.cancel) {
      await pending.locator('#cancel').click();
      check(await pending.locator('#confirm').isVisible(), 'Cancel must not dismiss an in-flight confirmed action');
    }
    observations.pendingAction = { disabled, dialogVisibleAfterEscape, actionCount };
    check(actionCount === 1, 'Only one confirmed action may be dispatched');
    await actionRoute.fulfill({ json: { outcomes: [{pid:101,status:'signalled'}], refused: [] } });
    await nextPaint(pending);
  } finally {
    await pending.close();
  }
  if (failures.length) throw new Error(JSON.stringify({ failures, observations }, null, 2));
  return { passed: true, observations };
}
