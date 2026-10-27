'use strict';

// Exercise the shipped inline script with deterministic DOM, storage and fetch
// doubles. Visual layout is checked separately in a browser; no npm install is
// needed for these request, draft, recovery and rendering regressions.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../app/index.html'), 'utf8');
const source = html.split('<script>')[1].split('</script>')[0];
const tick = () => new Promise(resolve => setImmediate(resolve));
const TASKS = [
  {id: 'a', objective: 'Task A', status: 'running'},
  {id: 'b', objective: 'Task B', status: 'paused'},
];

class Element {
  constructor(id) {
    this.id = id;
    this.value = '';
    this.textContent = '';
    this.dataset = {};
    this.children = [];
    this.disabled = false;
    this.className = '';
    this.attributes = {};
    this.scrollHeight = 0;
    this.scrollTop = 0;
    this.clientHeight = 0;
    this.listeners = {};
    this.content = {cloneNode: () => new Element('welcome-content')};
  }
  append(...children) { this.children.push(...children); }
  prepend(...children) { this.children.unshift(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  querySelectorAll(selector) { return selector === '[data-example]' ? this.exampleButtons || [] : []; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  focus() {}
}

function response(body, status = 200) {
  return {
    ok: status < 400, status,
    json: async () => body,
    text: async () => String(body),
  };
}

function idleResponse(url) {
  if (url.startsWith('/api/task/')) {
    return response({meta: {objective: url.slice(10), status: 'paused'}, events: []});
  }
  return response({model: {model: 'test'}, tasks: TASKS});
}

function environment({fetch = async url => idleResponse(url), blockedStorage = false, saved = {}} = {}) {
  const elements = new Map(
    [...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Element(match[1])])
  );
  elements.get('hours').value = '10';
  elements.get('events').exampleButtons = [...html.matchAll(/data-example="([^"]+)"/g)].map(match => {
    const element = new Element(match[1]);
    element.dataset.example = match[1];
    return element;
  });
  const controls = ['pause', 'resume', 'stop'].map(action => {
    const element = new Element(action);
    element.dataset.action = action;
    return element;
  });
  const storage = new Map(Object.entries(saved));
  const context = vm.createContext({
    document: {
      getElementById: id => elements.get(id),
      createElement: id => new Element(id),
      querySelectorAll: () => controls,
    },
    localStorage: {
      getItem(key) {
        if (blockedStorage) throw Error('storage blocked');
        return storage.get(key) ?? null;
      },
      setItem(key, value) {
        if (blockedStorage) throw Error('storage blocked');
        storage.set(key, value);
      },
      removeItem(key) {
        if (blockedStorage) throw Error('storage blocked');
        storage.delete(key);
      },
    },
    fetch, AbortSignal, Date, console, queueMicrotask,
    setInterval() {},
  });
  vm.runInContext(source, context);
  return {
    elements, storage,
    run: code => vm.runInContext(code, context),
    edit(text) {
      elements.get('prompt').value = text;
      elements.get('prompt').listeners.input();
    },
    select(id) {
      vm.runInContext('renderTasks(' + JSON.stringify(TASKS) + ')', context);
      const title = TASKS.find(task => task.id === id).objective;
      elements.get('tasks').children.find(button => button.title === title).onclick();
    },
  };
}

test('budgets accept 50 hours and reject empty, non-finite and out-of-range values', async () => {
  const ui = environment();
  await tick();
  for (const value of ['0.1', '10', '50', '72']) {
    ui.elements.get('hours').value = value;
    assert.equal(ui.run('readHours()'), Number(value));
  }
  for (const value of ['', '-1', '73', 'Infinity', 'invalid']) {
    ui.elements.get('hours').value = value;
    assert.throws(() => ui.run('readHours()'), /0.1 to 72/);
  }
});

test('the first-task example fills a saved, six-minute draft without starting work', async () => {
  let posts = 0;
  const ui = environment({fetch: async (url, options) => {
    if (options.method === 'POST') posts++;
    return idleResponse(url);
  }});
  await tick();
  ui.elements.get('events').exampleButtons.find(button => button.dataset.example === 'starter').onclick();
  assert.match(ui.elements.get('prompt').value, /Create welcome\.txt/);
  assert.match(ui.elements.get('prompt').value, /Use only file tools/);
  assert.equal(ui.elements.get('hours').value, '0.1');
  assert.equal(ui.storage.get('task-continuity-draft:new'), ui.elements.get('prompt').value);
  const restored = environment({saved: Object.fromEntries(ui.storage)});
  assert.equal(restored.elements.get('prompt').value, ui.elements.get('prompt').value);
  assert.equal(restored.elements.get('hours').value, '0.1');
  assert.match(ui.elements.get('notice').textContent, /six-minute limit/);
  assert.equal(ui.run('selected'), null);
  assert.equal(posts, 0);
});

test('choosing an example preserves an existing draft and its time limit', async () => {
  const ui = environment();
  await tick();
  ui.edit('A project I am still describing');
  ui.elements.get('hours').value = '50';
  ui.elements.get('events').exampleButtons.find(button => button.dataset.example === 'starter').onclick();
  assert.equal(ui.elements.get('prompt').value, 'A project I am still describing');
  assert.equal(ui.storage.get('task-continuity-draft:new'), 'A project I am still describing');
  assert.equal(ui.elements.get('hours').value, '50');
  assert.match(ui.elements.get('notice').textContent, /draft is still/);
});

test('drafts follow their task, including the separate new-task composer', async () => {
  const ui = environment();
  await tick();
  ui.edit('new goal');
  ui.select('a');
  assert.equal(ui.elements.get('prompt').value, '');
  ui.edit('follow up A');
  ui.select('b');
  ui.edit('follow up B');
  ui.select('a');
  assert.equal(ui.elements.get('prompt').value, 'follow up A');
  ui.run('newTask()');
  assert.equal(ui.elements.get('prompt').value, 'new goal');
  ui.select('b');
  assert.equal(ui.elements.get('prompt').value, 'follow up B');
  assert.equal(ui.storage.get('task-continuity-draft:a'), 'follow up A');
  assert.equal(ui.storage.get('task-continuity-draft:b'), 'follow up B');
});

test('blocked browser storage still preserves separate drafts within the tab', async () => {
  const ui = environment({blockedStorage: true});
  await tick();
  ui.edit('new draft');
  ui.select('a');
  ui.edit('A draft');
  ui.run('newTask()');
  assert.equal(ui.elements.get('prompt').value, 'new draft');
  ui.select('a');
  assert.equal(ui.elements.get('prompt').value, 'A draft');
});

test('reload restores the selected task draft and migrates the legacy single draft', async () => {
  const ui = environment({saved: {
    'task-continuity-selected': 'a',
    'task-continuity-draft': 'legacy follow-up',
  }});
  await tick();
  assert.equal(ui.elements.get('prompt').value, 'legacy follow-up');
  assert.equal(ui.storage.get('task-continuity-draft:a'), 'legacy follow-up');
  assert.equal(ui.storage.has('task-continuity-draft'), false);
  const restored = environment({saved: Object.fromEntries(ui.storage)});
  assert.equal(restored.elements.get('prompt').value, 'legacy follow-up');
});

test('successful sends clear the submitted draft and prevent duplicate in-flight sends', async () => {
  let release;
  let posts = 0;
  const ui = environment({fetch: async (url, options) => {
    if (url === '/api/start') {
      posts++;
      assert.equal(JSON.parse(options.body).hours, 50);
      return new Promise(resolve => { release = resolve; });
    }
    return idleResponse(url);
  }});
  await tick();
  ui.edit('a goal');
  ui.elements.get('hours').value = '50';
  const sending = ui.run('send()');
  assert.equal(ui.elements.get('send').attributes['aria-busy'], 'true');
  assert.equal(ui.elements.get('send').textContent, 'Starting…');
  await ui.run('send()');
  assert.equal(posts, 1);
  release(response({id: 'created'}));
  await sending;
  assert.equal(ui.run('selected'), 'created');
  assert.equal(ui.elements.get('prompt').value, '');
  assert.equal(ui.storage.has('task-continuity-draft:new'), false);
  assert.equal(ui.elements.get('send').disabled, false);
  assert.equal(ui.elements.get('send').attributes['aria-busy'], 'false');
  assert.equal(ui.elements.get('send').textContent, 'Send follow-up');
});

test('edits made while starting a task become its unsent follow-up draft', async () => {
  let release;
  const ui = environment({fetch: async url => {
    if (url === '/api/start') return new Promise(resolve => { release = resolve; });
    return idleResponse(url);
  }});
  await tick();
  ui.edit('original goal');
  const sending = ui.run('send()');
  ui.edit('still typing the next instruction');
  release(response({id: 'created'}));
  await sending;
  assert.equal(ui.elements.get('prompt').value, 'still typing the next instruction');
  assert.equal(ui.storage.get('task-continuity-draft:created'), 'still typing the next instruction');
  ui.run('newTask()');
  assert.equal(ui.elements.get('prompt').value, '');
});

test('sending a follow-up never clears a draft in a different selected task', async () => {
  let release;
  const ui = environment({fetch: async url => {
    if (url === '/api/followup') return new Promise(resolve => { release = resolve; });
    return idleResponse(url);
  }});
  await tick();
  ui.select('a');
  await tick();
  ui.edit('send to A');
  const sending = ui.run('send()');
  ui.select('b');
  ui.edit('B is not ready');
  release(response({ok: true}));
  await sending;
  assert.equal(ui.run('selected'), 'b');
  assert.equal(ui.elements.get('prompt').value, 'B is not ready');
  assert.equal(ui.storage.has('task-continuity-draft:a'), false);
});

test('a new-task selection during a send cannot be replaced by its old response', async () => {
  let release;
  const ui = environment({fetch: async url => {
    if (url === '/api/start') return new Promise(resolve => { release = resolve; });
    return idleResponse(url);
  }});
  await tick();
  ui.edit('goal');
  const sending = ui.run('send()');
  ui.run('newTask()');
  release(response({id: 'old-result'}));
  await sending;
  assert.equal(ui.run('selected'), null);
  assert.equal(ui.elements.get('prompt').value, 'goal');
});

test('failed sends keep their draft and restore the send button', async () => {
  const ui = environment({fetch: async url => url === '/api/start'
    ? response({error: 'Task could not start.'}, 400) : idleResponse(url)});
  await tick();
  ui.edit('unfinished goal');
  await ui.run('send()');
  assert.equal(ui.elements.get('prompt').value, 'unfinished goal');
  assert.equal(ui.storage.get('task-continuity-draft:new'), 'unfinished goal');
  assert.equal(ui.elements.get('notice').textContent, 'Task could not start.');
  assert.equal(ui.elements.get('send').disabled, false);
});

test('supervisor token rotation retries a rejected write exactly once', async () => {
  const writes = [];
  const ui = environment({fetch: async (url, options) => {
    if (url === '/api/action') {
      writes.push(options);
      return options.headers['X-Agent-Token'] === 'fresh'
        ? response({ok: true}) : response({error: 'stale'}, 403);
    }
    if (url === '/') return response("let TOKEN = 'fresh'");
    return idleResponse(url);
  }});
  await ui.run("api('/api/action', {id:'a'})");
  assert.equal(writes.length, 2);
  assert.equal(writes[0].body, writes[1].body);
  assert.equal(ui.run('TOKEN'), 'fresh');

  let rejected = 0;
  const denied = environment({fetch: async url => {
    if (url === '/api/action') { rejected++; return response({error: 'denied'}, 403); }
    if (url === '/') return response("let TOKEN = 'another-token'");
    return idleResponse(url);
  }});
  await assert.rejects(denied.run("api('/api/action', {id:'a'})"), /denied/);
  assert.equal(rejected, 2);
});

test('invalid API data is reported without blindly retrying a write', async () => {
  for (const body of [null, [], 'not an object']) {
    let posts = 0;
    const ui = environment({fetch: async url => {
      if (url === '/api/action') { posts++; return response(body); }
      return idleResponse(url);
    }});
    await assert.rejects(ui.run("api('/api/action', {id:'a'})"), /invalid response/);
    assert.equal(posts, 1);
  }
});

test('stale task data and control failures cannot overwrite a new selection', async () => {
  let releaseTask;
  let rejectAction;
  const ui = environment({fetch: async url => {
    if (url === '/api/task/a') return new Promise(resolve => { releaseTask = resolve; });
    if (url === '/api/stop') return new Promise((resolve, reject) => { rejectAction = reject; });
    return idleResponse(url);
  }});
  await tick();
  ui.run("selected='a'; selectionVersion++; taskHeading('A')");
  const loading = ui.run('renderSelectedTask()');
  const stopping = ui.run("action('stop')");
  ui.run("selected='b'; selectionVersion++; taskHeading('B')");
  releaseTask(response({meta: {objective:'old A', status:'running'}, events:[]}));
  rejectAction(Error('old A error'));
  await Promise.all([loading, stopping]);
  assert.equal(ui.elements.get('task-title').textContent, 'B');
  assert.equal(ui.elements.get('notice').textContent, '');
});

test('deleted selections restore the new-task draft without discarding the old one', async () => {
  const ui = environment({saved: {
    'task-continuity-selected': 'missing',
    'task-continuity-draft:missing': 'saved follow-up',
    'task-continuity-draft:new': 'next goal',
  }, fetch: async url => url.startsWith('/api/task/')
    ? response({error: 'Task not found.'}, 404) : idleResponse(url)});
  await tick();
  assert.equal(ui.run('selected'), null);
  assert.equal(ui.elements.get('prompt').value, 'next goal');
  assert.equal(ui.storage.get('task-continuity-draft:missing'), 'saved follow-up');
  assert.match(ui.elements.get('notice').textContent, /no longer exists/);
});

test('unchanged polls retain task and output nodes and hide internal test tasks', async () => {
  const ui = environment({fetch: async url => url.startsWith('/api/task/')
    ? response({meta: {status:'completed'}, events:[], result: {files:[{path:'result.txt'}]}})
    : idleResponse(url)});
  await tick();
  ui.run('renderTasks(' + JSON.stringify([...TASKS, {id:'test',internal_test:true}]) + ')');
  assert.equal(ui.elements.get('tasks').children.length, 2);
  const task = ui.elements.get('tasks').children[0];
  ui.run('renderTasks(' + JSON.stringify(TASKS) + ')');
  assert.equal(ui.elements.get('tasks').children[0], task);
  ui.run("selected='a'; selectionVersion++");
  await ui.run('renderSelectedTask()');
  const output = ui.elements.get('outputs').children[0];
  await ui.run('renderSelectedTask()');
  assert.equal(ui.elements.get('outputs').children[0], output);
});

test('long histories have bounded DOM size and event text remains literal', async () => {
  const history = Array.from({length: 1200}, (_, index) => ({role:'assistant',text:'Entry ' + index}));
  history.at(-1).text = '<img src=x onerror=alert(1)>';
  const ui = environment({fetch: async url => url.startsWith('/api/task/')
    ? response({meta: {status:'running'}, events:history}) : idleResponse(url)});
  await tick();
  ui.run("selected='a'; selectionVersion++");
  await ui.run('renderSelectedTask()');
  const children = ui.elements.get('events').children;
  assert.equal(children.length, 501);
  assert.match(children[0].textContent, /latest 500/);
  assert.equal(children[1].textContent, 'Entry 700');
  assert.equal(children.at(-1).textContent, '<img src=x onerror=alert(1)>');
});
