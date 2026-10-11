const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const listeners = new Map();
const observed = [];
const sections = [new Set(), new Set()].map(classes => ({
  classes,
  classList: { add: name => classes.add(name) },
}));
let onIntersection;
const context = vm.createContext({
  Date,
  document: {
    addEventListener: (name, callback) => listeners.set(name, callback),
    querySelectorAll: selector => {
      assert.equal(selector, '.landing-section-inner');
      return sections;
    },
  },
  IntersectionObserver: class {
    constructor(callback, options) {
      onIntersection = callback;
      assert.equal(options.threshold, 0.1);
    }
    observe(element) { observed.push(element); }
  },
});
vm.runInContext(readFileSync('app/static/js/landing.js', 'utf8'), context);

test('landing animation observes sections after DOM readiness and reveals intersecting sections', () => {
  assert.equal(observed.length, 0);
  listeners.get('DOMContentLoaded')();
  assert.deepEqual(observed, sections);
  onIntersection([
    { target: sections[0], isIntersecting: true },
    { target: sections[1], isIntersecting: false },
  ]);
  assert.ok(sections[0].classes.has('section-visible'));
  assert.equal(sections[1].classes.has('section-visible'), false);
});

test('discovery combines case-insensitive name search with open voting filter', () => {
  const page = context.landingPage([
    { name: 'Komunitas Kreatif', is_voting_open: true },
    { name: 'Komunitas Kampus', is_voting_open: false },
    { name: 'Fotografi', is_voting_open: true },
  ]);
  page.query = ' KOMUNITAS ';
  assert.equal(page.filteredEvents.length, 2);
  page.openOnly = true;
  assert.equal(page.filteredEvents.length, 1);
  assert.equal(page.filteredEvents[0].name, 'Komunitas Kreatif');
  page.query = 'missing';
  assert.equal(page.filteredEvents.length, 0);
  page.query = '';
  assert.equal(page.filteredEvents.length, 2);
});

test('unscheduled events have an honest identity instead of an invented date', () => {
  const page = context.landingPage();
  assert.equal(page.dateMonth({}), 'Event');
  assert.equal(page.dateDay({}), 'RVD');
  assert.equal(page.filteredEvents.length, 0);
});

test('featured event countdown supports scheduled and expired events', () => {
  const future = context.eventCountdown({closes_at: new Date(Date.now() + 2 * 86400000).toISOString()});
  future._updateCountdown();
  assert.ok(Number(future.countdown.days) >= 1);
  const invalid = context.eventCountdown({closes_at: 'invalid'});
  invalid._updateCountdown();
  assert.equal(invalid.countdown.days, '00');
});
