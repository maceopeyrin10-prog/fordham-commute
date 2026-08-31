/**
 * The alarm clock for the Fordham commute tool.
 *
 * Why this exists: GitHub runs the actual work perfectly when asked, but its
 * own scheduler throttled a "every 5 minutes" request down to roughly one run
 * a day, which is useless for catching a track number in a 19-minute window.
 *
 * So Google keeps time instead. This runs on Google's servers every 5 minutes
 * and simply presses GitHub's "Run workflow" button. Your laptop is not
 * involved and can be shut, flat, or in a drawer.
 *
 * Setup is in the README under "The alarm clock".
 */

const OWNER = 'maceopeyrin10-prog';
const REPO = 'fordham-commute';
const WORKFLOW = 'commute.yml';

// Only bother between these hours, New York time. Outside them the commute
// tool would decide there is nothing to do anyway, so we save the calls.
const START_HOUR = 6;
const END_HOUR = 19;

function pressTheButton() {
  const now = new Date();
  const hour = Number(Utilities.formatDate(now, 'America/New_York', 'H'));
  const day = Number(Utilities.formatDate(now, 'America/New_York', 'u')); // 1=Mon

  if (day > 5) return;                          // weekends: no class
  if (hour < START_HOUR || hour >= END_HOUR) return;

  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  if (!token) {
    throw new Error('No GITHUB_TOKEN set. Project Settings -> Script Properties.');
  }

  const url = 'https://api.github.com/repos/' + OWNER + '/' + REPO +
              '/actions/workflows/' + WORKFLOW + '/dispatches';

  const response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + token,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28'
    },
    payload: JSON.stringify({ ref: 'main' }),
    muteHttpExceptions: true
  });

  const code = response.getResponseCode();
  if (code !== 204) {
    // 204 is success and returns no body. Anything else is worth seeing in
    // the Apps Script execution log.
    console.error('GitHub said ' + code + ': ' + response.getContentText());
  }
}

/**
 * Run this ONCE by hand to install the every-5-minutes timer.
 * Safe to run again; it clears any timer it already made first.
 */
function installTimer() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'pressTheButton') ScriptApp.deleteTrigger(t);
  });

  ScriptApp.newTrigger('pressTheButton').timeBased().everyMinutes(5).create();

  console.log('Timer installed. It will press the button every 5 minutes.');
  pressTheButton();
  console.log('Test press sent. Check the repository Actions tab.');
}
