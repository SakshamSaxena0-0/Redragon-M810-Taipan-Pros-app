/* Taipan Studio UI. Talks to Python through window.pywebview.api; Python pushes events into app.on(kind, data). */
'use strict';
const $ = (s, r = document) => r.querySelector(s);
const clone = o => JSON.parse(JSON.stringify(o));

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'vars') for (const [n, x] of Object.entries(v)) el.style.setProperty(n, x);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat(9)) if (c != null && c !== false) el.append(c.nodeType ? c : document.createTextNode(String(c)));
  return el;
}

let API = null;
const S = {
  status: {}, saved: null, prof: null, factory: null, page: 'perf', sel: 0, showHidden: false,
  buttons: [], hidden: [], media: [], pointer: null, presets: [], backups: [], settings: {},
  macros: [], selMac: null, rec: null, mock: false, warnings: [],
  bg: null, cbEdit: null, cbRec: false, cbKeys: null, apps: null, icons: {},
};

const TABS = [['perf', 'Performance'], ['light', 'Lighting'], ['buttons', 'Buttons'], ['macros', 'Macros'], ['combos', 'Shortcuts'],
              ['pointer', 'Pointer'], ['profiles', 'Profiles']];
// button markers in the mouse SVG's viewBox (400 x 660), in S.buttons order:
// left, right, wheel, side front, side rear, DPI up, DPI down, fire
const HOTSPOTS = [[128, 130], [308, 130], [218.5, 146], [42, 262], [62, 356], [218.5, 259.5], [218.5, 319], [69.5, 126]];
const COLORS = ['#ff2a55', '#ff7a1a', '#ffd400', '#7dff4d', '#00ffa3', '#00e5ff', '#2f7bff', '#7a5cff', '#d14dff', '#ff4fd8', '#ffffff'];
const MEDIA_LABEL = {
  play_pause: 'Play / Pause', next: 'Next track', previous: 'Previous track', stop: 'Stop', mute: 'Mute',
  volume_up: 'Volume up', volume_down: 'Volume down', media_player: 'Media player', mail: 'Mail',
  calculator: 'Calculator', my_computer: 'This PC', web_search: 'Web search', web_home: 'Browser home',
  web_back: 'Browser back', web_forward: 'Browser forward', web_stop: 'Browser stop', web_refresh: 'Browser refresh',
  web_favorites: 'Favorites',
};

/* ------------------------------------------------------------------ helpers */
let toastT = 0;
function toast(msg, bad) {
  const t = $('#toast');
  t.textContent = msg; t.classList.toggle('bad', !!bad); t.classList.add('show');
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove('show'), bad ? 5200 : 2600);
}
function pct(v, a, b) { return ((v - a) / (b - a) * 100).toFixed(2) + '%'; }
function range(min, max, step, value, oninput, onchange, color) {
  const r = h('input', { type: 'range', min, max, step, value });
  const paint = () => { r.style.setProperty('--p', pct(+r.value, min, max)); if (color) r.style.setProperty('--c', color); };
  paint();
  r.addEventListener('input', () => { paint(); oninput && oninput(+r.value); });
  if (onchange) r.addEventListener('change', () => onchange(+r.value));
  return r;
}
function chip(label, on, onclick, cls = 'chip') { return h('button', { class: `clear ${cls}${on ? ' on' : ''}`, onclick }, label); }
function toggle(on, onclick) { return h('button', { class: 'toggle' + (on ? ' on' : ''), role: 'switch', 'aria-checked': on ? 'true' : 'false', onclick }); }
function card(title, sub, right, ...body) {
  return h('div', { class: 'card' }, h('div', { class: 'card-h' }, h('h3', null, title), sub && h('span', { class: 'sub' }, sub),
    right && h('div', { class: 'right' }, right)), ...body);
}
function fmtDpi(x, y) { return x === y ? `${x}` : `${x} × ${y}`; }

/* canonical form used for the unsaved-changes check */
function canon(p) {
  if (!p) return '';
  const b = p.buttons.map(a => {
    const c = Object.assign({}, a); delete c.lib; delete c.slot; return c;
  });
  const macros = p.buttons.map((a, i) => a.kind === 'macro' && p.macros && p.macros[i]
    ? { name: (p.macros[i].name || '').slice(0, 15), events: p.macros[i].events.map(e => [e.type, e.code, !!e.down, e.delay]) } : null);
  return JSON.stringify([p.polling, p.stage_count, p.stages.slice(0, 5).map(s => [s.x, s.y, s.color.toLowerCase()]),
    p.dpi_led.mode === 'off' ? 'off' : p.dpi_led, p.main_led.mode === 'off' ? 'off' : [p.main_led.mode, p.main_led.color.toLowerCase(), p.main_led.speed, p.main_led.brightness],
    b, macros]);
}
const dirty = () => S.saved ? canon(S.prof) !== canon(S.saved) : false;

/* ------------------------------------------------------------------ action names */
function actionName(a) {
  switch (a.kind) {
    case 'disabled': return 'Disabled';
    case 'mouse': return { left: 'Left click', right: 'Right click', middle: 'Middle click', back: 'Back', forward: 'Forward' }[a.button];
    case 'double': return 'Double click';
    case 'triple': return 'Triple click';
    case 'fire': return `Fire key · ${a.interval} ms × ${a.times}`;
    case 'dpi': return { up: 'DPI up', down: 'DPI down', cycle: 'DPI cycle' }[a.op];
    case 'keys': return [...(a.mods || []).map(HID.modName), ...(a.keys || []).map(HID.name)].join(' + ') || 'Keystroke (empty)';
    case 'media': return MEDIA_LABEL[a.key] || `Media 0x${Number(a.key).toString(16)}`;
    case 'macro': {
      const m = S.prof.macros && S.prof.macros[a.slot];
      const rep = a.repeat === 'hold' ? 'while held' : a.repeat === 'toggle' ? 'until pressed again' : a.repeat > 1 ? `× ${a.repeat}` : 'once';
      return `Macro · ${m && m.name ? m.name : 'unnamed'} (${rep})`;
    }
    case 'polling': return 'Polling-rate switch';
    case 'rgb': return 'Lighting on / off';
    case 'profile': return 'Profile switch';
    default: return `Raw ${a.type}/${a.a}/${a.b}`;
  }
}

/* ------------------------------------------------------------------ chrome: status, save bar, mouse visual */
function renderStatus() {
  const st = S.status, pill = $('#statusPill');
  let cls = 'bad', text = 'Receiver not found — plug in the 2.4G receiver';
  if (st.receiver && !st.online) { cls = 'sleep'; text = 'Mouse asleep — move it to wake it'; }
  if (st.online) { cls = 'ok'; text = `Connected · ${S.mock ? 'demo mouse' : st.kind || '2.4G'}`; }
  if (st.conflict) { cls = 'bad'; text = 'Close the Redragon app (OemDrv) — it is using the mouse'; }
  if (st.busy) text = st.busy;
  pill.className = 'pill ' + cls;
  $('#statusText').textContent = text;
  renderSaveBar();
}
function renderSaveBar() {
  const bar = $('#savebar'), d = dirty(), st = S.status;
  bar.classList.toggle('dirty', d);
  bar.classList.toggle('busy', !!st.busy);
  let t = 'All changes are on the mouse';
  if (!S.saved) t = st.online ? 'Reading the mouse…' : 'Wake the mouse to load its settings';
  else if (d) t = st.online ? 'Unsaved changes' : 'Unsaved changes — wake the mouse to save';
  if (st.busy) t = st.busy;
  $('#saveText').textContent = t;
  $('#btnSave').disabled = !d || !st.online || !!st.busy;
  $('#btnRevert').disabled = !d || !!st.busy;
}
function changed(rerender = true) {
  renderSaveBar(); renderMouse();
  if (rerender) renderPage();
}

function renderMouse() {
  const p = S.prof; if (!p) return;
  const stage = Math.min(p.stage || 0, p.stage_count - 1), st = p.stages[stage];
  $('#dpiNow').textContent = fmtDpi(st.x, st.y);
  $('#dpiStageLabel').textContent = `Stage ${stage + 1} of ${p.stage_count}`;
  const dots = $('#stageDots'); dots.textContent = '';
  for (let i = 0; i < p.stage_count; i++) dots.append(h('i', { class: i === stage ? 'on' : '', style: `background:${p.stages[i].color};color:${p.stages[i].color}` }));
  const svg = $('#mouseSvg'), m = p.main_led, d = p.dpi_led;
  svg.setAttribute('class', `fx-${m.mode} dfx-${d.mode}`);
  svg.style.setProperty('--led-color', m.color);
  svg.style.setProperty('--led-period', (6.6 - m.speed * 0.56).toFixed(2) + 's');
  svg.style.setProperty('--led-bright', (0.25 + m.brightness * 0.075).toFixed(2));
  svg.style.setProperty('--dpi-color', st.color);
  svg.style.setProperty('--dpi-period', (6.6 - d.speed * 0.56).toFixed(2) + 's');
  svg.style.setProperty('--dpi-bright', (0.2 + d.brightness / 255 * 0.8).toFixed(2));
  const hs = $('#hotspots'); hs.textContent = '';
  S.buttons.forEach((b, i) => {
    const [x, y] = HOTSPOTS[i];
    hs.append(h('button', {
      class: 'clear hot' + (S.page === 'buttons' && S.sel === b.slot ? ' sel' : ''), style: `left:${x / 4}%;top:${y / 6.6}%`,
      onclick: () => { S.sel = b.slot; setPage('buttons'); }, 'aria-label': b.name,
    }, String(i + 1), h('span', { class: 'tip' }, `${b.name}: ${actionName(p.buttons[b.slot])}`)));
  });
  $('#stageFoot').textContent = '';
  if (S.mock) $('#stageFoot').append('Demo mode · ', h('a', { href: '#', style: 'color:var(--accent)', onclick: e => {
    e.preventDefault(); API.demo_dpi((stage + 1) % p.stage_count);
  } }, 'press the DPI button'));
  else $('#stageFoot').textContent = 'Click a number to remap that button';
}

let hudT = 0;
function showHud(stage) {
  const st = S.prof.stages[stage];
  $('#hudDpi').textContent = fmtDpi(st.x, st.y);
  $('#hudSub').textContent = `DPI · Stage ${stage + 1}`;
  $('#hud').style.boxShadow = `0 0 40px ${st.color}55, var(--shadow)`;
  $('#hud').classList.add('show'); clearTimeout(hudT); hudT = setTimeout(() => $('#hud').classList.remove('show'), 1500);
}

/* ------------------------------------------------------------------ pages */
function setPage(p) { if (p !== 'combos') { cbRecord(false); S.cbKeys = null; } S.page = p; renderTabs(); renderPage(); renderMouse(); }
function renderTabs() {
  const n = $('#tabs'); n.textContent = '';
  for (const [id, label] of TABS) n.append(h('button', { class: S.page === id ? 'on' : '', role: 'tab', onclick: () => setPage(id) }, label));
}
function renderPage() {
  const el = $('#pages'), top = el.scrollTop; el.textContent = '';
  if (!S.prof) { el.append(h('div', { class: 'empty' }, 'Waiting for the mouse…')); return; }
  el.append(({ perf: pagePerf, light: pageLight, buttons: pageButtons, macros: pageMacros, combos: pageCombos, pointer: pagePointer, profiles: pageProfiles })[S.page]());
  el.scrollTop = top;
}

/* --- performance */
function pagePerf() {
  const p = S.prof;
  const rows = p.stages.slice(0, 5).map((st, i) => {
    const xy = st.x !== st.y || st._xy;
    const setV = (axis, v, commit) => {
      if (axis === 'both') { st.x = st.y = v; } else st[axis] = v;
      if (commit) {
        API.dpi_actual(st.x, st.y).then(([x, y]) => {
          if (x !== st.x || y !== st.y) toast(`Stage ${i + 1} stored as ${fmtDpi(x, y)} DPI (above 5000 the mouse uses 200-DPI steps on both axes)`);
          st.x = x; st.y = y; changed();
        });
      } else { changed(false); }
    };
    const axis = (name, key) => {
      const num = h('input', { class: 'num', type: 'number', min: 200, max: 10000, step: 100, value: st[key] });
      const r = range(200, 10000, 100, st[key], v => { num.value = v; setV(xy ? key : 'both', v); }, v => setV(xy ? key : 'both', v, true), st.color);
      num.addEventListener('change', () => setV(xy ? key : 'both', Math.max(200, Math.min(10000, +num.value || 200)), true));
      return h('div', { class: 'ax' }, xy && h('em', null, name), r, num);
    };
    return h('div', { class: `stage-row${i >= p.stage_count ? ' off' : ''}${i === p.stage && i < p.stage_count ? ' cur' : ''}` },
      h('button', { class: 'sw', vars: { '--c': st.color }, title: 'Stage colour', onclick: e => colorPop(e.currentTarget, st.color, c => { st.color = c; changed(); }) }),
      h('div', { class: 'nm' }, `Stage ${i + 1}`, h('small', null, i === p.stage && i < p.stage_count ? 'active now' : i >= p.stage_count ? 'off' : '')),
      h('div', { class: 'xy' }, xy ? [axis('X', 'x'), axis('Y', 'y')] : axis('', 'x')),
      h('div', null, i === p.stage && i < p.stage_count ? h('span', { class: 'badge' }, 'ACTIVE') : ''),
      h('button', { class: 'clear chip sm' + (xy ? ' on' : ''), title: 'Separate X and Y sensitivity',
        onclick: () => { if (xy) { st.y = st.x; st._xy = false; } else st._xy = true; changed(); } }, 'X / Y'));
  });
  return h('div', { class: 'page' },
    h('h2', null, 'Performance'),
    h('p', { class: 'lede' }, 'DPI stages cycle with the DPI buttons on the mouse. Each stage has its own indicator colour.'),
    card('DPI stages', '200 – 10 000 DPI', null,
      h('div', { class: 'row', style: 'margin:-4px 0 10px' }, h('span', { class: 'hint' }, 'Use'), [1, 2, 3, 4, 5].map(n => chip(String(n), p.stage_count === n, () => {
        p.stage_count = n; if (p.stage >= n) p.stage = 0; changed();
      }, 'chip sm')), h('span', { class: 'hint' }, 'stages in the DPI-button cycle')),
      rows),
    card('Polling rate', 'How often the mouse reports its position', null,
      h('div', { class: 'chips' }, [125, 250, 500, 1000].map(hz => chip(`${hz} Hz`, p.polling === hz, () => { p.polling = hz; changed(); }))),
      h('p', { class: 'hint', style: 'margin:10px 0 0' }, '1000 Hz is smoothest; lower rates save battery on 2.4G.')));
}

/* --- colour popover */
function colorPop(anchor, value, onpick) {
  const pop = $('#pop'); pop.textContent = '';
  const hex = h('input', { class: 'num hex', value: value.toUpperCase(), maxlength: 7 });
  const pick = c => { onpick(c); closePop(); };
  hex.addEventListener('change', () => { if (/^#?[0-9a-f]{6}$/i.test(hex.value)) pick('#' + hex.value.replace('#', '').toLowerCase()); });
  pop.append(h('div', { class: 'swatches' }, COLORS.map(c => h('button', { class: 'swatch' + (c === value.toLowerCase() ? ' on' : ''), vars: { '--c': c }, onclick: () => pick(c) })),
    h('label', { class: 'picker', title: 'Any colour' }, h('input', { type: 'color', value, onchange: e => pick(e.target.value) }))),
    h('div', { class: 'row', style: 'margin-top:12px' }, h('span', { class: 'hint' }, 'Hex'), hex));
  const r = anchor.getBoundingClientRect();
  pop.classList.add('show');
  pop.style.left = Math.min(r.left, innerWidth - pop.offsetWidth - 12) + 'px';
  pop.style.top = Math.min(r.bottom + 8, innerHeight - pop.offsetHeight - 12) + 'px';
  setTimeout(() => document.addEventListener('mousedown', outside), 0);
}
function outside(e) { if (!$('#pop').contains(e.target)) closePop(); }
function closePop() { $('#pop').classList.remove('show'); document.removeEventListener('mousedown', outside); }

function colorRow(value, onpick) {
  return h('div', { class: 'swatches' }, COLORS.map(c => h('button', { class: 'swatch' + (c === value.toLowerCase() ? ' on' : ''), vars: { '--c': c }, onclick: () => onpick(c) })),
    h('label', { class: 'picker', title: 'Any colour' }, h('input', { type: 'color', value, oninput: e => onpick(e.target.value, true), onchange: e => onpick(e.target.value) })),
    h('input', { class: 'num hex', value: value.toUpperCase(), maxlength: 7, onchange: e => { if (/^#?[0-9a-f]{6}$/i.test(e.target.value)) onpick('#' + e.target.value.replace('#', '').toLowerCase()); } }));
}

/* --- lighting */
function pageLight() {
  const m = S.prof.main_led, d = S.prof.dpi_led;
  const modes = [['streaming', 'Streaming', 'Rainbow flow'], ['breathing', 'Breathing', 'Fades in and out'], ['steady', 'Steady', 'Solid colour'], ['off', 'Off', 'Lights out']];
  const dmodes = [['steady', 'Steady', 'Shows the stage colour'], ['breathing', 'Breathing', 'Pulses the stage colour'], ['off', 'Off', 'No indicator']];
  return h('div', { class: 'page' },
    h('h2', null, 'Lighting'),
    h('p', { class: 'lede' }, 'The preview on the left follows your choices. Streaming uses its own rainbow, so the colour only applies to Breathing and Steady.'),
    card('Side strips & wheel', null, null,
      h('div', { class: 'grid2' }, modes.map(([id, n, s]) => h('button', { class: 'clear tile' + (m.mode === id ? ' on' : ''), onclick: () => { m.mode = id; changed(); } }, h('b', null, n), h('span', null, s)))),
      m.mode !== 'off' && m.mode !== 'streaming' && h('div', { class: 'row', style: 'margin-top:16px' }, h('span', { class: 'label' }, 'Colour'),
        colorRow(m.color, (c, live) => { m.color = c; changed(!live); })),
      m.mode !== 'off' && m.mode !== 'steady' && h('div', { class: 'row', style: 'margin-top:14px' }, h('span', { class: 'label' }, 'Speed'),
        h('span', { class: 'hint' }, 'Slow'), range(1, 10, 1, m.speed, v => { m.speed = v; changed(false); }, () => changed()), h('span', { class: 'hint' }, 'Fast')),
      m.mode !== 'off' && h('div', { class: 'row', style: 'margin-top:12px' }, h('span', { class: 'label' }, 'Brightness'),
        h('span', { class: 'hint' }, 'Dim'), range(1, 10, 1, m.brightness, v => { m.brightness = v; changed(false); }, () => changed()), h('span', { class: 'hint' }, 'Bright'))),
    card('DPI indicator', 'Uses each stage’s colour from Performance', null,
      h('div', { class: 'grid2' }, dmodes.map(([id, n, s]) => h('button', { class: 'clear tile' + (d.mode === id ? ' on' : ''), onclick: () => { d.mode = id; changed(); } }, h('b', null, n), h('span', null, s)))),
      d.mode === 'breathing' && h('div', { class: 'row', style: 'margin-top:16px' }, h('span', { class: 'label' }, 'Speed'),
        h('span', { class: 'hint' }, '1'), range(1, 10, 1, d.speed, v => { d.speed = v; changed(false); }, () => changed()), h('span', { class: 'hint' }, '10')),
      d.mode !== 'off' && h('div', { class: 'row', style: 'margin-top:12px' }, h('span', { class: 'label' }, 'Brightness'),
        h('span', { class: 'hint' }, 'Dim'), range(10, 255, 1, d.brightness, v => { d.brightness = v; changed(false); }, () => changed()), h('span', { class: 'hint' }, 'Bright'))));
}

/* --- buttons */
const CATS = [['mouse', 'Mouse'], ['keys', 'Keystroke'], ['media', 'Media'], ['dpi', 'DPI'], ['fire', 'Fire key'], ['macro', 'Macro'], ['special', 'Special'], ['disabled', 'Disable']];
function catOf(a) {
  if (['mouse', 'double', 'triple'].includes(a.kind)) return 'mouse';
  if (['polling', 'rgb', 'profile', 'raw'].includes(a.kind)) return 'special';
  return a.kind;
}
let capture = null;
function pageButtons() {
  const p = S.prof, list = S.buttons.concat(S.showHidden ? S.hidden : []);
  if (!list.some(b => b.slot === S.sel)) S.sel = list[0].slot;
  const a = p.buttons[S.sel], cat = S.cat && S.catFor === S.sel ? S.cat : catOf(a);
  const set = act => { p.buttons[S.sel] = act; changed(); };
  const items = list.map(b => {
    const i = S.buttons.indexOf(b);
    return h('button', { class: 'clear btn-item' + (b.slot === S.sel ? ' on' : ''), onclick: () => { S.sel = b.slot; S.cat = null; changed(); } },
      h('span', { class: 'n' }, i >= 0 ? String(i + 1) : '•'), h('span', { class: 't' }, h('b', null, b.name), h('span', null, actionName(p.buttons[b.slot]))));
  });
  const body = [];
  if (cat === 'mouse') {
    const opts = [['left', 'Left click'], ['right', 'Right click'], ['middle', 'Middle click'], ['back', 'Back'], ['forward', 'Forward']];
    body.push(h('div', { class: 'grid2' }, opts.map(([b, n]) => h('button', { class: 'clear tile' + (a.kind === 'mouse' && a.button === b ? ' on' : ''), onclick: () => set({ kind: 'mouse', button: b }) }, h('b', null, n))),
      h('button', { class: 'clear tile' + (a.kind === 'double' ? ' on' : ''), onclick: () => set({ kind: 'double' }) }, h('b', null, 'Double click'), h('span', null, 'Two clicks per press')),
      h('button', { class: 'clear tile' + (a.kind === 'triple' ? ' on' : ''), onclick: () => set({ kind: 'triple' }) }, h('b', null, 'Triple click'), h('span', null, 'Three clicks per press'))));
  } else if (cat === 'keys') {
    const cur = a.kind === 'keys' ? a : { kind: 'keys', mods: [], keys: [] };
    const box = h('div', { class: 'capture' + (capture ? ' live' : ''), tabindex: 0,
      onclick: () => { capture = { mods: [], keys: [] }; changed(); } },
      capture ? (capture.mods.length || capture.keys.length ? keyChips(capture) : 'Press the keys now…')
        : (cur.mods.length || cur.keys.length ? keyChips(cur) : 'Click here, then press a key or combination'));
    body.push(box, h('p', { class: 'hint' }, 'Up to 3 keys plus Ctrl, Shift, Alt and Win. Release all keys to finish.'),
      h('div', { class: 'row' }, h('span', { class: 'label' }, 'Extra keys'),
        h('select', { class: 'text', style: 'max-width:220px', onchange: e => { if (e.target.value) set({ kind: 'keys', mods: cur.mods, keys: [+e.target.value] }); } },
          h('option', { value: '' }, 'F13 – F24 (not on keyboards)…'), HID.extra.map(k => h('option', { value: k.usage }, k.name))),
        h('span', { class: 'hint' }, 'Great for push-to-talk or OBS hotkeys that never clash')));
  } else if (cat === 'media') {
    body.push(h('div', { class: 'grid2' }, S.media.map(k => h('button', { class: 'clear tile' + (a.kind === 'media' && a.key === k ? ' on' : ''), onclick: () => set({ kind: 'media', key: k }) }, h('b', null, MEDIA_LABEL[k] || k)))));
  } else if (cat === 'dpi') {
    body.push(h('div', { class: 'grid2' }, [['up', 'DPI up', 'Next stage'], ['down', 'DPI down', 'Previous stage'], ['cycle', 'DPI cycle', 'Loops through stages']].map(([o, n, s]) =>
      h('button', { class: 'clear tile' + (a.kind === 'dpi' && a.op === o ? ' on' : ''), onclick: () => set({ kind: 'dpi', op: o }) }, h('b', null, n), h('span', null, s)))));
  } else if (cat === 'fire') {
    const f = a.kind === 'fire' ? a : { kind: 'fire', interval: 58, times: 3 };
    if (a.kind !== 'fire') body.push(h('button', { class: 'btn primary', onclick: () => set(f) }, 'Use fire key on this button'));
    else body.push(
      h('div', { class: 'row' }, h('span', { class: 'label' }, 'Interval'), range(3, 255, 1, f.interval, v => { f.interval = v; $('#fv').textContent = v + ' ms'; changed(false); }, () => changed()), h('span', { id: 'fv', class: 'hint', style: 'width:60px' }, f.interval + ' ms')),
      h('div', { class: 'row' }, h('span', { class: 'label' }, 'Times'), h('div', { class: 'chips' }, [0, 1, 2, 3].map(n => chip(String(n), f.times === n, () => { f.times = n; changed(); }, 'chip sm')))),
      h('p', { class: 'hint' }, 'Rapid left clicks for each press. The ranges match the Redragon app (interval 3–255, times 0–3).'));
  } else if (cat === 'macro') {
    const pick = h('select', { class: 'text', onchange: e => assignMacro(S.sel, e.target.value, a.kind === 'macro' ? a.repeat : 1) },
      h('option', { value: '' }, S.macros.length ? 'Choose a macro…' : 'No macros yet — record one on the Macros tab'),
      S.macros.map(m => h('option', { value: m.id, selected: a.kind === 'macro' && a.lib === m.id ? true : null }, m.name)));
    body.push(h('div', { class: 'row' }, h('span', { class: 'label' }, 'Macro'), pick));
    if (a.kind === 'macro') {
      const reps = [[1, 'Once'], [3, '3 times'], ['hold', 'While held'], ['toggle', 'Until pressed again']];
      body.push(h('div', { class: 'row' }, h('span', { class: 'label' }, 'Repeat'), h('div', { class: 'chips' }, reps.map(([r, n]) => chip(n, a.repeat === r, () => { a.repeat = r; changed(); }, 'chip sm')),
        h('input', { class: 'num', type: 'number', min: 1, max: 253, value: typeof a.repeat === 'number' ? a.repeat : '', placeholder: 'n×', title: 'Exact number of times',
          onchange: e => { a.repeat = Math.max(1, Math.min(253, +e.target.value || 1)); changed(); } }))));
    }
    body.push(h('button', { class: 'btn ghost sm', style: 'margin-top:12px', onclick: () => setPage('macros') }, 'Record or edit macros →'));
  } else if (cat === 'special') {
    body.push(h('div', { class: 'grid2' }, [['polling', 'Polling-rate switch', 'Cycles 125 – 1000 Hz'], ['rgb', 'Lighting on / off', 'Toggles all LEDs'], ['profile', 'Profile switch', 'Mouse-side profile']].map(([k, n, s]) =>
      h('button', { class: 'clear tile' + (a.kind === k ? ' on' : ''), onclick: () => set({ kind: k }) }, h('b', null, n), h('span', null, s)))));
    if (a.kind === 'raw') body.push(h('p', { class: 'hint' }, `This button has a code Taipan Studio doesn't know (${a.type}/${a.a}/${a.b}); it is kept as is.`));
  } else if (cat === 'disabled') {
    body.push(h('button', { class: 'btn ' + (a.kind === 'disabled' ? 'ghost' : 'danger'), onclick: () => set({ kind: 'disabled' }) }, a.kind === 'disabled' ? 'This button is disabled' : 'Disable this button'));
  }
  const name = list.find(b => b.slot === S.sel).name;
  return h('div', { class: 'page' },
    h('h2', null, 'Buttons'),
    h('p', { class: 'lede' }, 'Pick a button, then what it should do. Numbers match the bubbles on the mouse.'),
    h('div', { class: 'btn-layout' },
      h('div', { class: 'btn-list' }, items,
        h('button', { class: 'btn ghost sm', style: 'margin-top:6px', onclick: () => { S.showHidden = !S.showHidden; changed(); } }, S.showHidden ? 'Hide extra slots' : 'Show 2 extra slots (bottom button…)')),
      card(name, actionName(a), null,
        h('div', { class: 'seg', style: 'margin:0 0 16px;background:rgba(0,0,0,.18)' }, CATS.map(([id, n]) => h('button', { class: cat === id ? 'on' : '', onclick: () => { S.cat = id; S.catFor = S.sel; capture = null; renderPage(); } }, n))),
        body)));
}
function keyChips(k) { return h('div', null, [...k.mods.map(HID.modName), ...k.keys.map(HID.name)].map(t => h('span', { class: 'kbd' }, t))); }

const MOD_ORDER = ['ctrl', 'shift', 'alt', 'win'];
window.addEventListener('keydown', e => {
  if (S.cbKeys) { cbKey(e, true); return; }
  if (capture) {
    e.preventDefault();
    const m = HID.mod(e.code);
    if (m) { const base = m.replace(/^r/, ''); if (!capture.mods.includes(base)) capture.mods.push(base); }
    else { const u = HID.usage(e.code); if (u && !capture.keys.includes(u) && capture.keys.length < 3) capture.keys.push(u); }
    capture.down = (capture.down || 0) + (e.repeat ? 0 : 1);
    renderPage(); return;
  }
  if (S.rec) { recKey(e, true); return; }
}, true);
window.addEventListener('keyup', e => {
  if (S.cbKeys) { cbKey(e, false); return; }
  if (capture) {
    e.preventDefault();
    capture.down = (capture.down || 1) - 1;
    if (capture.down <= 0 && (capture.mods.length || capture.keys.length)) {
      S.prof.buttons[S.sel] = { kind: 'keys', mods: MOD_ORDER.filter(x => capture.mods.includes(x)), keys: capture.keys };
      capture = null; changed();
    }
    return;
  }
  if (S.rec) recKey(e, false);
}, true);

/* --- macros (library on this PC; each button slot stores its own copy on the mouse) */
function saveLibrary() { API.set_setting('macros', S.macros); }
function assignMacro(slot, id, repeat) {
  const m = S.macros.find(x => x.id === id); if (!m) return;
  S.prof.buttons[slot] = { kind: 'macro', slot, repeat: repeat || 1, lib: id };
  S.prof.macros[slot] = { name: m.name, events: clone(m.events) };
  changed();
}
function syncMacro(m) {
  S.prof.buttons.forEach((a, slot) => { if (a.kind === 'macro' && a.lib === m.id) S.prof.macros[slot] = { name: m.name, events: clone(m.events) }; });
}
function recKey(e, down) {
  e.preventDefault();
  if (e.repeat) return;
  const m = HID.mod(e.code);
  recPush(m ? { type: 'mod', code: m, down } : { type: 'key', code: HID.usage(e.code), down });
}
function recPush(ev) {
  const r = S.rec; if (!r || ev.code == null) return;
  const now = performance.now(), m = S.macros.find(x => x.id === S.selMac);
  if (!m || m.events.length >= 69) return;
  if (m.events.length) m.events[m.events.length - 1].delay = r.fixed ? r.fixed : Math.max(3, Math.min(65535, Math.round(now - r.t)));
  ev.delay = r.fixed || 10; r.t = now;
  m.events.push(ev); renderPage();
}
function evName(e) {
  if (e.type === 'mod') return HID.modName(e.code);
  if (e.type === 'mouse') return { left: 'Left button', right: 'Right button', middle: 'Middle button', back: 'Back button', forward: 'Forward button' }[e.code] || e.code;
  return HID.name(e.code);
}
function pageMacros() {
  let m = S.macros.find(x => x.id === S.selMac);
  if (!m && S.macros.length) { m = S.macros[0]; S.selMac = m.id; }
  const fromMouse = S.prof.buttons.map((a, slot) => a.kind === 'macro' && !a.lib && S.prof.macros[slot] ? { slot, mac: S.prof.macros[slot] } : null).filter(Boolean);
  const list = h('div', { class: 'mac-list' },
    h('button', { class: 'btn primary', onclick: () => {
      const id = 'm' + Date.now().toString(36); S.macros.push({ id, name: 'Macro ' + (S.macros.length + 1), events: [] });
      S.selMac = id; saveLibrary(); renderPage();
    } }, '+ New macro'),
    S.macros.map(x => h('button', { class: 'clear btn-item' + (x.id === S.selMac ? ' on' : ''), onclick: () => { stopRec(); S.selMac = x.id; renderPage(); } },
      h('span', { class: 't' }, h('b', null, x.name), h('span', null, `${x.events.length} events`)))),
    fromMouse.length ? h('p', { class: 'hint', style: 'margin:10px 4px 2px' }, 'Found on the mouse') : null,
    fromMouse.map(({ slot, mac }) => h('button', { class: 'clear btn-item', title: 'Copy into your library', onclick: () => {
      const id = 'm' + Date.now().toString(36); S.macros.push({ id, name: mac.name || `Slot ${slot + 1} macro`, events: clone(mac.events) });
      S.prof.buttons[slot].lib = id; S.selMac = id; saveLibrary(); renderPage();
    } }, h('span', { class: 't' }, h('b', null, mac.name || `Slot ${slot + 1}`), h('span', null, `${mac.events.length} events · click to import`)))));
  if (!m) return h('div', { class: 'page' }, h('h2', null, 'Macros'), h('p', { class: 'lede' }, 'Record key and mouse sequences, then assign them to a button.'), h('div', { class: 'mac-layout' }, list, h('div', { class: 'card empty' }, 'Create a macro to get started.')));
  const rec = !!S.rec;
  const pad = h('div', { class: 'capture' + (rec ? ' live' : ''), oncontextmenu: e => e.preventDefault(),
    onmousedown: e => { if (rec) { e.preventDefault(); recPush({ type: 'mouse', code: ['left', 'middle', 'right', 'back', 'forward'][e.button], down: true }); } },
    onmouseup: e => { if (rec) { e.preventDefault(); recPush({ type: 'mouse', code: ['left', 'middle', 'right', 'back', 'forward'][e.button], down: false }); } } },
    rec ? 'Recording — type keys, or click inside this box for mouse buttons' : 'Press Record, then type. Mouse clicks are recorded inside this box.');
  const evs = h('div', { class: 'ev-list' }, m.events.length ? m.events.map((e, i) => h('div', { class: 'ev' },
    h('span', { class: 'arrow ' + (e.down ? 'dn' : 'up') }, e.down ? '↓' : '↑'),
    h('span', null, evName(e)),
    h('input', { class: 'num', type: 'number', min: 3, max: 65535, value: e.delay, title: 'Delay after this event (ms)',
      onchange: ev => { e.delay = Math.max(3, Math.min(65535, +ev.target.value || 3)); syncMacro(m); saveLibrary(); changed(); } }),
    h('button', { class: 'x', title: 'Remove', onclick: () => { m.events.splice(i, 1); syncMacro(m); saveLibrary(); changed(); } }, '✕'))) : h('div', { class: 'empty' }, 'No events yet'));
  const fixedIn = h('input', { class: 'num', type: 'number', min: 3, max: 65535, placeholder: 'real', title: 'Leave empty to record real timing', value: S.fixedDelay || '',
    onchange: e => { S.fixedDelay = +e.target.value || 0; } });
  const users = S.prof.buttons.map((a, slot) => a.kind === 'macro' && a.lib === m.id ? (S.buttons.concat(S.hidden).find(b => b.slot === slot) || {}).name : null).filter(Boolean);
  return h('div', { class: 'page' + (rec ? ' recording' : '') },
    h('h2', null, 'Macros'),
    h('p', { class: 'lede' }, 'Macros live in this app; assigning one to a button copies it into that button’s memory on the mouse (up to 69 events each).'),
    h('div', { class: 'mac-layout' }, list,
      card(h('input', { class: 'text', value: m.name, maxlength: 15, style: 'font-weight:600;max-width:220px',
        onchange: e => { m.name = e.target.value.trim() || m.name; syncMacro(m); saveLibrary(); changed(); } }),
        users.length ? 'On: ' + users.join(', ') : 'Not assigned to a button',
        [h('button', { class: 'btn ' + (rec ? 'danger' : 'ghost') + ' sm', onclick: () => rec ? stopRec() : startRec() }, h('span', { class: 'rec' }), rec ? 'Stop' : 'Record'),
         h('button', { class: 'btn ghost sm', onclick: () => { m.events = []; syncMacro(m); saveLibrary(); changed(); } }, 'Clear'),
         h('button', { class: 'btn danger sm', onclick: () => {
           if (users.length) { toast('Unassign this macro from its buttons first', true); return; }
           S.macros = S.macros.filter(x => x !== m); S.selMac = null; saveLibrary(); renderPage();
         } }, 'Delete')],
        pad,
        h('div', { class: 'row', style: 'margin:12px 0' }, h('span', { class: 'hint' }, 'Fixed delay (ms)'), fixedIn,
          h('button', { class: 'btn ghost sm', onclick: () => { const v = S.fixedDelay || 20; m.events.forEach(e => { e.delay = v; }); syncMacro(m); saveLibrary(); changed(); } }, 'Apply to all'),
          h('span', { class: 'hint', style: 'margin-left:auto' }, `${m.events.length} / 69 events`)),
        evs,
        h('div', { class: 'row', style: 'margin-top:12px' }, h('span', { class: 'hint' }, 'Assign to'),
          h('select', { class: 'text', style: 'max-width:240px', onchange: e => { if (e.target.value !== '') { assignMacro(+e.target.value, m.id, 1); toast('Assigned — save to mouse to keep it'); } } },
            h('option', { value: '' }, 'Choose a button…'), S.buttons.concat(S.hidden).map(b => h('option', { value: b.slot }, b.name)))))));
}
function startRec() { const m = S.macros.find(x => x.id === S.selMac); if (!m) return; S.rec = { t: performance.now(), fixed: S.fixedDelay || 0 }; renderPage(); }
function stopRec() { if (!S.rec) return; S.rec = null; const m = S.macros.find(x => x.id === S.selMac); if (m) { syncMacro(m); saveLibrary(); } changed(); }

/* --- shortcuts (combos.py): run by Taipan Studio itself, nothing is stored on the mouse */
const CB_NAME = { left: 'Left click', right: 'Right click', middle: 'Wheel click', back: 'Back (MB4)', forward: 'Forward (MB5)',
                  wheel_up: 'Scroll up', wheel_down: 'Scroll down', wheel_both: 'Scroll up / down' };
const CB_STARTERS = [['forward', 'Forward (MB5)', 'front side button'], ['back', 'Back (MB4)', 'rear side button'], ['middle', 'Wheel click', 'press the wheel']];
const CB_ACTIONS = [['volume_up', 'Volume up'], ['volume_down', 'Volume down'], ['mute', 'Mute'], ['play_pause', 'Play / Pause'],
  ['next', 'Next track'], ['previous', 'Previous track'], ['launch', 'Open app or file'], ['url', 'Open website'],
  ['keys', 'Key shortcut'], ['lock', 'Lock PC']];
const CB_MEDIA = ['volume_up', 'volume_down', 'mute', 'play_pause', 'next', 'previous'];
// Scroll up and Scroll down on one button running the two halves of a pair show and edit as one shortcut.
// 'wheel_both' and {type: 'pair'} exist only on this page; they are saved as two combos.
// [up action, down action, full name, short name, what it does]
const CB_PAIRS = { volume: ['volume_up', 'volume_down', 'Volume up / down', 'Volume', 'Louder / quieter'],
                   track: ['next', 'previous', 'Next / previous track', 'Tracks', 'Next / previous'] };
const VOL_STEPS = [[1, '2%'], [2, '4%'], [3, '6%'], [5, '10%']];
const MOD_VK = { 17: 'Ctrl', 16: 'Shift', 18: 'Alt', 91: 'Win' };
const WHEEL_TOKENS = ['wheel_up', 'wheel_down'];
const GLYPHS = {
  volume: '<path d="M4 9.5v5h3.5l4.5 4v-13l-4.5 4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
  mute: '<path d="M4 9.5v5h3.5l4.5 4v-13l-4.5 4z"/><path d="M16 9.5l5 5M21 9.5l-5 5"/>',
  track: '<path d="M6 6.5v11l8.5-5.5z"/><path d="M18 6.5v11"/>',
  play: '<path d="M7 5.5v13l10.5-6.5z"/>',
  url: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.4 2.4 3.5 5.3 3.5 8.5s-1.1 6.1-3.5 8.5c-2.4-2.4-3.5-5.3-3.5-8.5s1.1-6.1 3.5-8.5z"/>',
  keys: '<rect x="3" y="6.5" width="18" height="11" rx="2.5"/><path d="M7 10.5h.01M10.5 10.5h.01M14 10.5h.01M17 10.5h.01M8 14h8"/>',
  lock: '<rect x="5" y="10.5" width="14" height="9.5" rx="2.5"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
  launch: '<rect x="4" y="4" width="16" height="16" rx="4"/><path d="M4 9h16"/>',
  edit: '<path d="M4 20h4L18.5 9.5l-4-4L4 16z"/><path d="M13 7l4 4"/>',
  trash: '<path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12"/>',
};
const MEDIA_GLYPH = { volume_up: 'volume', volume_down: 'volume', mute: 'mute', play_pause: 'play', next: 'track', previous: 'track' };

function glyph(name, cls = '') {
  const s = h('span', { class: 'glyph ' + cls });
  s.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${GLYPHS[name]}</svg>`;
  return s;
}
function cbKind(a) { return !a ? '' : a.type === 'media' ? a.key : a.type === 'pair' ? 'pair:' + a.key : a.type; }
function cbActionName(a) {
  if (!a) return 'Choose an action';
  const pct = /^volume/.test(a.key) ? ` · ${(a.step || 1) * 2}%` : '';
  if (a.type === 'pair') return CB_PAIRS[a.key][2] + pct;
  if (a.type === 'media') return MEDIA_LABEL[a.key] + pct;
  if (a.type === 'launch') return a.target ? 'Open ' + a.target.split(/[\\/]/).pop() : 'Open app or file';
  if (a.type === 'url') return a.url ? 'Open ' + a.url.replace(/^https?:\/\//, '').replace(/\/$/, '') : 'Open website';
  if (a.type === 'keys') return a.label || 'Keyboard shortcut';
  if (a.type === 'lock') return 'Lock PC';
  return a.type;
}
function cbGesture(starter, seq) {
  const out = [h('span', { class: 'kbd' }, 'Hold ' + CB_NAME[starter])];
  seq.forEach((t, i) => out.push(h('span', { class: 'cb-op' }, i ? '→' : '+'), h('span', { class: 'kbd' }, CB_NAME[t])));
  return h('div', { class: 'cb-g' }, out);
}
// the saved combo list as the shortcuts the page shows: {starter, seq, action, items: the combos behind it}
function cbShortcuts(list) {
  const out = [], used = new Set(), isWheel = c => c.seq.length === 1 && WHEEL_TOKENS.includes(c.seq[0]);
  for (const c of list) {
    if (used.has(c)) continue;
    used.add(c);
    const other = isWheel(c) && list.find(o => !used.has(o) && isWheel(o) && o.starter === c.starter && o.seq[0] !== c.seq[0]);
    if (other) {
      const [up, down] = c.seq[0] === 'wheel_up' ? [c, other] : [other, c];
      const pair = Object.keys(CB_PAIRS).find(k => up.action.type === 'media' && down.action.type === 'media' &&
        up.action.key === CB_PAIRS[k][0] && down.action.key === CB_PAIRS[k][1] && (up.action.step || 1) === (down.action.step || 1));
      if (pair) {
        used.add(other);
        out.push({ starter: c.starter, seq: ['wheel_both'], items: [up, down], action: { type: 'pair', key: pair, step: up.action.step || 1 } });
        continue;
      }
    }
    out.push({ starter: c.starter, seq: c.seq, items: [c], action: c.action });
  }
  return out;
}
// change the editor's gesture, turning a pair action into its single half (or back) to match
function cbSetSeq(ed, seq) {
  const a = ed.action, both = seq[0] === 'wheel_both';
  if (both && a && a.type !== 'pair') {
    const k = a.type === 'media' && Object.keys(CB_PAIRS).find(p => CB_PAIRS[p].slice(0, 2).includes(a.key));
    ed.action = k ? { type: 'pair', key: k, step: a.step || 1 } : null;
  } else if (!both && a && a.type === 'pair') {
    ed.action = { type: 'media', key: CB_PAIRS[a.key][seq[0] === 'wheel_down' ? 1 : 0], step: a.step || 1 };
  }
  ed.seq = seq;
}
function cbSave(cfg) { return API.set_combos(cfg).then(r => { S.bg = r; renderPage(); }); }
function cbRecord(on) {
  if (S.cbRec === on) return;
  S.cbRec = on; API.record_combo(on);
  if (S.page === 'combos') renderPage();
}
function loadApps() { API.running_apps().then(r => { S.apps = r; if (S.page === 'combos') renderPage(); }); }
function setKeys(list) {      // list of [vk, name]; modifiers first, in Ctrl, Shift, Alt, Win order
  const order = [17, 16, 18, 91], mods = order.filter(v => list.some(k => k[0] === v)).map(v => [v, MOD_VK[v]]);
  const keys = list.filter(k => !order.includes(k[0]));
  const all = mods.concat(keys);
  S.cbEdit.action = { type: 'keys', vks: all.map(k => k[0]), names: all.map(k => k[1]), label: all.map(k => k[1]).join(' + ') };
}
function cbKey(e, down) {
  e.preventDefault();
  const k = S.cbKeys;
  if (down) {
    if (e.repeat) return;
    k.down++;
    const vk = e.keyCode === 92 ? 91 : e.keyCode;
    const name = MOD_VK[vk] || (HID.usage(e.code) ? HID.name(HID.usage(e.code)) : e.key.length === 1 ? e.key.toUpperCase() : e.key);
    if (vk && !k.list.some(x => x[0] === vk) && k.list.length < 5) k.list.push([vk, name]);
  } else if (--k.down <= 0 && k.list.length) {
    setKeys(k.list); S.cbKeys = null;
  }
  renderPage();
}
function cbCommit() {
  const ed = S.cbEdit, cfg = S.bg.combos, a = ed.action;
  if (!ed.seq.length) return toast('Record the shortcut, or pick a scroll or some clicks', true);
  if (!a) return toast('Choose what the shortcut does', true);
  if (a.type === 'launch' && !a.target) return toast('Pick an app or file to open', true);
  if (a.type === 'url' && !a.url) return toast('Type a website address', true);
  if (a.type === 'keys' && !(a.vks || []).some(v => !MOD_VK[v])) return toast('Press a key for the shortcut', true);
  const stamp = Date.now().toString(36);
  const items = a.type === 'pair'
    ? WHEEL_TOKENS.map((t, i) => ({ id: ed.ids[i] || `c${stamp}${i}`, starter: ed.starter, seq: [t],
                                   action: { type: 'media', key: CB_PAIRS[a.key][i], step: a.step || 1 } }))
    : [{ id: ed.ids[0] || 'c' + stamp, starter: ed.starter, seq: ed.seq, action: a }];
  const rest = cfg.list.filter(c => !ed.ids.includes(c.id));
  for (const it of items) {
    const clash = rest.find(c => c.starter === it.starter && c.seq.join() === it.seq.join());
    if (clash) return toast(`Hold ${CB_NAME[it.starter]} + ${it.seq.map(t => CB_NAME[t]).join(' → ')} already does “${cbActionName(clash.action)}”`, true);
  }
  const at = cfg.list.findIndex(c => ed.ids.includes(c.id));      // an edited shortcut keeps its place
  rest.splice(at < 0 ? rest.length : at, 0, ...items);
  cfg.list = rest;
  S.cbEdit = null; S.cbKeys = null; cbRecord(false);
  cbSave(cfg).then(() => toast(cfg.enabled ? 'Shortcut saved — try it now' : 'Shortcut saved — turn shortcuts on to use it'));
}

function comboEditor() {
  const ed = S.cbEdit, a = ed.action, kind = cbKind(a), rec = S.cbRec;
  const wheel = ed.seq.length === 1 && ed.seq[0].startsWith('wheel'), both = ed.seq[0] === 'wheel_both';
  const setSeq = seq => { cbSetSeq(ed, seq); renderPage(); };
  const pickAction = k => {
    if (both) ed.action = { type: 'pair', key: k, step: a && a.step || 1 };
    else if (CB_MEDIA.includes(k)) ed.action = { type: 'media', key: k, step: a && a.step || 1 };
    else ed.action = { launch: { type: 'launch', target: '' }, url: { type: 'url', url: '' }, keys: { type: 'keys', vks: [], names: [], label: '' }, lock: { type: 'lock' } }[k];
    S.cbKeys = null; renderPage();
  };
  const extra = [];
  if (/^volume_(up|down)$/.test(kind) || kind === 'pair:volume') {
    extra.push(h('div', { class: 'row' }, h('span', { class: 'label' }, 'Step'),
      h('div', { class: 'chips' }, VOL_STEPS.map(([n, l]) => chip(l, (a.step || 1) === n, () => { a.step = n; renderPage(); }, 'chip sm'))),
      h('span', { class: 'hint' }, wheel ? 'per wheel notch' : 'each time')));
  } else if (kind === 'launch') {
    extra.push(h('div', { class: 'row' }, h('span', { class: 'label' }, 'Open'),
      h('input', { class: 'text', value: a.target, placeholder: 'C:\\Path\\to\\app.exe, a shortcut, a file or a folder', oninput: e => { a.target = e.target.value; } }),
      h('button', { class: 'btn ghost sm', onclick: () => API.pick_target().then(p => { if (p) { a.target = p; renderPage(); } }) }, 'Browse…')));
  } else if (kind === 'url') {
    extra.push(h('div', { class: 'row' }, h('span', { class: 'label' }, 'Website'),
      h('input', { class: 'text', value: a.url, placeholder: 'youtube.com', oninput: e => { a.url = e.target.value; } })));
  } else if (kind === 'keys') {
    const k = S.cbKeys, cur = a.names || [];
    extra.push(h('div', { class: 'capture' + (k ? ' live' : ''), onclick: () => { S.cbKeys = { list: [], down: 0 }; renderPage(); } },
      k ? (k.list.length ? h('div', null, k.list.map(x => h('span', { class: 'kbd' }, x[1]))) : 'Press the keys now…')
        : cur.length ? h('div', null, cur.map(n => h('span', { class: 'kbd' }, n))) : 'Click here, then press a shortcut'),
      h('div', { class: 'row', style: 'margin-top:10px' }, h('span', { class: 'hint' }, 'Add'), [17, 16, 18, 91].map(v => chip(MOD_VK[v], (a.vks || []).includes(v), () => {
        const list = (a.vks || []).map((x, i) => [x, (a.names || [])[i] || MOD_VK[x] || 'Key']);
        setKeys(list.some(x => x[0] === v) ? list.filter(x => x[0] !== v) : list.concat([[v, MOD_VK[v]]])); renderPage();
      }, 'chip sm')), h('span', { class: 'hint' }, 'Win is easier to add here than to press')));
  }
  const actions = both ? Object.entries(CB_PAIRS).map(([k, p]) => ['pair:' + k, p[3], p[4]]) : CB_ACTIONS;
  const editor = card(ed.ids.length ? 'Edit shortcut' : 'New shortcut', null, null,
    h('div', { class: 'capture' + (rec ? ' live' : ''), onclick: () => cbRecord(!rec) },
      rec ? 'Recording… hold a mouse button, then scroll or click. Click here to cancel.'
        : ed.seq.length ? h('div', { class: 'cb-rec' }, cbGesture(ed.starter, ed.seq), h('small', null, 'Click to record again'))
        : 'Click here, then do the shortcut on your mouse'),
    h('div', { class: 'row', style: 'margin-top:14px' }, h('span', { class: 'label' }, 'Hold'),
      h('div', { class: 'chips grow' }, CB_STARTERS.map(([id, n, sub]) => chip(n, ed.starter === id, () => {
        ed.starter = id; ed.seq = ed.seq.filter(t => t !== id); renderPage();
      }, 'chip sm'))), h('span', { class: 'hint' }, (CB_STARTERS.find(s => s[0] === ed.starter) || [])[2])),
    h('div', { class: 'row' }, h('span', { class: 'label' }, 'Then'),
      h('div', { class: 'chips grow' }, ['wheel_both', ...WHEEL_TOKENS].map(t => chip(CB_NAME[t], wheel && ed.seq[0] === t, () => setSeq([t]), 'chip sm')),
        h('span', { class: 'cb-op' }, 'or click'),
        ['left', 'right', 'middle', 'back', 'forward'].filter(b => b !== ed.starter).map(b => chip('+ ' + CB_NAME[b].replace(/ click$/, ''), false,
          () => setSeq((wheel ? [] : ed.seq).concat([b]).slice(0, 6)), 'chip sm')),
        ed.seq.length ? chip('Clear', false, () => setSeq([]), 'chip sm') : null)),
    h('div', { class: 'row', style: 'align-items:flex-start' }, h('span', { class: 'label', style: 'padding-top:10px' }, 'Do'),
      h('div', { class: 'grid2 cb-acts' }, actions.map(([k, n, what]) => h('button', { class: 'clear tile' + (kind === k ? ' on' : ''),
        onclick: () => pickAction(k.replace(/^pair:/, '')) }, h('b', null, n), what && h('span', null, what))))),
    extra.length ? h('div', { style: 'margin-top:14px' }, extra) : null,
    h('div', { class: 'row', style: 'margin-top:16px' },
      h('button', { class: 'btn primary', onclick: cbCommit }, ed.ids.length ? 'Save shortcut' : 'Add shortcut'),
      h('button', { class: 'btn ghost', disabled: !a, onclick: () => API.try_action(a.type === 'pair' ? { type: 'media', key: CB_PAIRS[a.key][0], step: a.step } : a) }, 'Try the action'),
      h('button', { class: 'btn ghost', onclick: () => { S.cbEdit = null; S.cbKeys = null; cbRecord(false); renderPage(); } }, 'Cancel')));
  editor.classList.add('sc-editor');
  return editor;
}

function cbIcon(a) {
  if (a.type === 'launch' && a.target) {
    if (!(a.target in S.icons)) {
      S.icons[a.target] = null;
      API.target_icon(a.target).then(u => { S.icons[a.target] = u || ''; if (S.page === 'combos') renderPage(); });
    }
    if (S.icons[a.target]) return h('img', { src: S.icons[a.target], alt: '' });
  }
  return glyph(a.type === 'pair' ? a.key : a.type === 'media' ? MEDIA_GLYPH[a.key] : GLYPHS[a.type] ? a.type : 'launch',
               a.key === 'previous' ? 'flip' : '');
}
function cbDescribe(a, wheel) {     // [title, line under it]
  const pct = `${(a.step || 1) * 2}%`;
  if (a.type === 'pair') return [CB_PAIRS[a.key][3], a.key === 'volume' ? `${pct} per notch` : CB_PAIRS[a.key][4]];
  if (a.type === 'media') return [MEDIA_LABEL[a.key], /^volume/.test(a.key) ? `${pct} ${wheel ? 'per notch' : 'each time'}` : 'Media key'];
  if (a.type === 'launch') { const f = a.target.split(/[\\/]/).pop(); return [f.replace(/\.(exe|lnk|url)$/i, '') || 'App', 'Opens ' + f]; }
  if (a.type === 'url') return [a.url.replace(/^https?:\/\//, '').replace(/\/$/, ''), 'Opens in your browser'];
  if (a.type === 'keys') return [a.label || 'Key shortcut', 'Presses these keys'];
  if (a.type === 'lock') return ['Lock PC', 'Locks Windows'];
  return [a.type, ''];
}
function cbTile(s) {
  const ids = s.items.map(c => c.id), sel = S.cbEdit && ids.some(id => S.cbEdit.ids.includes(id));
  const [title, sub] = cbDescribe(s.action, s.seq[0].startsWith('wheel'));
  return h('div', { class: 'sc-tile' + (sel ? ' sel' : '') },
    h('div', { class: 'sc-head' }, h('div', { class: 'sc-ico' }, cbIcon(s.action)),
      h('div', { class: 'sc-t' }, h('b', { title }, title), h('span', { title: s.action.target || sub }, sub))),
    cbGesture(s.starter, s.seq),
    h('div', { class: 'sc-tools' },
      h('button', { title: 'Edit', onclick: () => {
        S.cbKeys = null; cbRecord(false);
        S.cbEdit = { ids, starter: s.starter, seq: s.seq.slice(), action: clone(s.action) }; renderPage();
        requestAnimationFrame(() => $('.sc-editor') && $('.sc-editor').scrollIntoView({ behavior: 'smooth', block: 'nearest' }));
      } }, glyph('edit')),
      h('button', { class: 'del', title: 'Delete', onclick: () => {
        const cfg = S.bg.combos;
        cfg.list = cfg.list.filter(c => !ids.includes(c.id)); if (sel) S.cbEdit = null; cbSave(cfg);
      } }, glyph('trash'))));
}

function pageCombos() {
  const bg = S.bg, cfg = bg.combos, list = cbShortcuts(cfg.list);
  if (!S.apps) { S.apps = []; loadApps(); }
  const addable = (S.apps || []).filter(x => !cfg.exclude.includes(x));
  return h('div', { class: 'page' },
    h('h2', null, 'Shortcuts'),
    h('p', { class: 'lede' }, 'Hold a mouse button, then scroll or click to run something in any app. Shortcuts run in Taipan Studio, not on the mouse, so they work while the app is open or in the tray. Tapping the button on its own still does its normal job.'),
    card('Your shortcuts', cfg.enabled ? `${list.length} on` : 'Off', toggle(cfg.enabled, () => { cfg.enabled = !cfg.enabled; cbSave(cfg); }),
      h('div', { class: 'sc-grid' + (cfg.enabled ? '' : ' dim') }, list.map(cbTile),
        !S.cbEdit && h('button', { class: 'clear sc-new', onclick: () => {
          S.cbEdit = { ids: [], starter: 'forward', seq: [], action: null }; renderPage();
          requestAnimationFrame(() => $('.sc-editor') && $('.sc-editor').scrollIntoView({ behavior: 'smooth', block: 'nearest' }));
        } }, h('b', null, '+'), 'New shortcut'))),
    S.cbEdit && comboEditor(),
    card('Pause in these apps', 'For games that use your side buttons', null,
      h('div', { class: 'chips' }, cfg.exclude.length ? cfg.exclude.map(x => h('span', { class: 'kbd cb-ex' }, x,
        h('button', { class: 'x', title: 'Remove', onclick: () => { cfg.exclude = cfg.exclude.filter(y => y !== x); cbSave(cfg); } }, '✕')))
        : h('span', { class: 'hint' }, 'None, so shortcuts work everywhere.')),
      h('div', { class: 'row', style: 'margin-top:12px' },
        h('select', { class: 'text', style: 'max-width:280px', onchange: e => { if (e.target.value) { cfg.exclude.push(e.target.value); cbSave(cfg); } } },
          h('option', { value: '' }, addable.length ? 'Add an open app…' : 'No other apps open'), addable.map(x => h('option', { value: x }, x))),
        h('button', { class: 'btn ghost sm', onclick: loadApps }, 'Refresh list'))),
    card('Background', null, null,
      h('div', { class: 'setting' }, h('div', null, h('b', null, 'Keep running in the tray'), h('span', null, 'Closing the window hides it, so shortcuts keep working. Quit from the tray icon.')),
        toggle(bg.tray, () => API.set_tray(!bg.tray).then(r => { S.bg = r; renderPage(); }))),
      h('div', { class: 'setting' }, h('div', null, h('b', null, 'Start with Windows'), h('span', null, bg.can_autostart ? 'Opens hidden in the tray when you sign in.' : 'Available in the built app (Taipan Studio.exe).')),
        bg.can_autostart && toggle(bg.autostart, () => API.set_autostart(!bg.autostart).then(r => { S.bg = r; renderPage(); }))),
      h('p', { class: 'hint', style: 'margin:12px 0 0' }, bg.native
        ? 'Light on CPU: a tiny native filter passes every mouse movement straight on, so only clicks and scrolls reach the app. Between clicks it uses no CPU at all.'
        : 'Only clicks and scrolls are looked at; between them the app uses no CPU.')));
}

/* --- Windows pointer settings (system-wide, applied immediately) */
function pagePointer() {
  const p = S.pointer || {};
  const set = patch => API.set_pointer(patch).then(r => { S.pointer = r; });
  return h('div', { class: 'page' },
    h('h2', null, 'Windows pointer'),
    h('p', { class: 'lede' }, 'These are Windows settings, applied instantly to every mouse. They are not stored on the M810.'),
    card('Pointer', null, null,
      h('div', { class: 'row' }, h('span', { class: 'label' }, 'Speed'), range(1, 20, 1, p.speed || 10, v => { $('#psv').textContent = v; }, v => set({ speed: v })), h('span', { id: 'psv', class: 'hint', style: 'width:30px' }, p.speed || 10)),
      h('p', { class: 'hint' }, '10 is neutral (1:1). Change DPI on the mouse instead for the cleanest tracking.'),
      h('div', { class: 'row' }, h('span', { class: 'label' }, 'Enhance pointer precision'),
        toggle(p.precision, () => { set({ precision: !p.precision }).then(() => renderPage()); }),
        h('span', { class: 'hint' }, 'Windows acceleration. Most players turn it off for consistent aim.'))),
    card('Wheel & clicks', null, null,
      h('div', { class: 'row' }, h('span', { class: 'label' }, 'Scroll lines'), range(1, 20, 1, p.scroll || 3, v => { $('#scv').textContent = v; }, v => set({ scroll: v })), h('span', { id: 'scv', class: 'hint', style: 'width:30px' }, p.scroll || 3)),
      h('div', { class: 'row' }, h('span', { class: 'label' }, 'Double-click'), h('span', { class: 'hint' }, 'Fast'),
        range(200, 900, 10, 1100 - (p.double_click || 500), v => { $('#dcv').textContent = (1100 - v) + ' ms'; }, v => set({ double_click: 1100 - v })), h('span', { class: 'hint' }, 'Slow'),
        h('span', { id: 'dcv', class: 'hint', style: 'width:60px' }, (p.double_click || 500) + ' ms'))));
}

/* --- profiles, presets & backups */
function pageProfiles() {
  const nameIn = h('input', { class: 'text', placeholder: 'Preset name', maxlength: 40 });
  return h('div', { class: 'page' },
    h('h2', null, 'Profiles & backup'),
    h('p', { class: 'lede' }, 'Keep as many setups as you like on this PC and load one onto the mouse in a click.'),
    card('Presets on this PC', `${S.presets.length} saved`, null,
      h('div', { class: 'row', style: 'margin-bottom:12px' }, nameIn, h('button', { class: 'btn primary sm', onclick: () => {
        const n = nameIn.value.trim(); if (!n) { nameIn.focus(); return; }
        API.save_preset(n, S.prof).then(r => { S.presets = r; toast(`Saved “${n}”`); renderPage(); });
      } }, 'Save current')),
      h('div', { class: 'list' }, S.presets.length ? S.presets.map(pr => h('div', { class: 'li' }, h('span', { class: 'grow' }, pr.name),
        h('button', { class: 'btn ghost sm', onclick: () => { loadProfile(pr.profile); toast(`Loaded “${pr.name}” — save to put it on the mouse`); } }, 'Load'),
        h('button', { class: 'btn danger sm', onclick: () => API.delete_preset(pr.file).then(r => { S.presets = r; renderPage(); }) }, 'Delete')))
        : h('div', { class: 'empty' }, 'No presets yet'))),
    card('Share', 'Profiles are plain JSON files', null, h('div', { class: 'row' },
      h('button', { class: 'btn ghost', onclick: () => API.export_profile(S.prof).then(r => r.ok && toast('Exported to ' + r.path)) }, 'Export…'),
      h('button', { class: 'btn ghost', onclick: () => API.import_profile().then(r => { if (r.ok) { loadProfile(r.profile); toast('Imported — save to put it on the mouse'); } else if (r.error) toast(r.error, true); }) }, 'Import…'),
      h('button', { class: 'btn ghost', onclick: () => { loadProfile(S.factory); toast('Factory settings loaded — save to apply'); } }, 'Factory settings'),
      h('button', { class: 'btn ghost', onclick: () => { API.reload(); toast('Re-reading the mouse…'); } }, 'Re-read mouse'))),
    card('Mouse memory backup', 'A full copy of the M810’s settings memory', null,
      h('p', { class: 'hint', style: 'margin-top:0' }, 'Taipan Studio makes one automatically before its first save. Restoring writes every byte back.'),
      h('div', { class: 'row', style: 'margin-bottom:12px' },
        h('button', { class: 'btn ghost', disabled: !S.status.online || !!S.status.busy, onclick: () => API.backup().then(r => { r.ok ? toast('Backup saved') : toast(r.error, true); refreshBackups(); }) }, 'Back up now'),
        h('button', { class: 'btn ghost', onclick: () => API.open_backups() }, 'Open folder')),
      h('div', { class: 'list' }, S.backups.length ? S.backups.map(b => h('div', { class: 'li' }, h('span', { class: 'grow' }, b.name),
        h('small', null, new Date(b.time * 1000).toLocaleString()),
        h('button', { class: 'btn ghost sm', disabled: !S.status.online || !!S.status.busy, onclick: () => confirmPop(`Restore ${b.name}? This overwrites everything on the mouse.`, () =>
          API.restore(b.path).then(r => r.ok ? toast('Backup restored') : toast(r.error, true))) }, 'Restore')))
        : h('div', { class: 'empty' }, 'No backups yet'))));
}
function confirmPop(msg, yes) {
  const pop = $('#pop'); pop.textContent = '';
  pop.append(h('p', { style: 'margin:0 0 12px;max-width:320px' }, msg), h('div', { class: 'row' },
    h('button', { class: 'btn danger sm', onclick: () => { closePop(); yes(); } }, 'Yes, restore'), h('button', { class: 'btn ghost sm', onclick: closePop }, 'Cancel')));
  pop.classList.add('show');
  pop.style.left = (innerWidth - pop.offsetWidth) / 2 + 'px'; pop.style.top = (innerHeight - pop.offsetHeight) / 2 + 'px';
  setTimeout(() => document.addEventListener('mousedown', outside), 0);
}
function refreshBackups() { API.backups().then(r => { S.backups = r; if (S.page === 'profiles') renderPage(); }); }
function loadProfile(p) {
  const stage = S.prof ? S.prof.stage : 0;
  S.prof = Object.assign(clone(S.factory), clone(p), { stage });
  if (!S.prof.macros) S.prof.macros = Array(16).fill(null);
  changed();
}

/* ------------------------------------------------------------------ save */
async function save() {
  const p = S.prof;
  if (!S.buttons.some(b => p.buttons[b.slot].kind === 'mouse' && p.buttons[b.slot].button === 'left')) {
    toast('At least one button must stay “Left click”', true); return;
  }
  for (const [slot, a] of p.buttons.entries()) if (a.kind === 'macro' && !(p.macros[slot] && p.macros[slot].events.length)) {
    toast('A button has an empty macro — record something first', true); return;
  }
  $('#btnSave').disabled = true;
  const r = await API.save(p);
  if (r.ok) {
    if (r.profile) { r.profile.buttons.forEach((a, i) => { if (a.kind === 'macro' && p.buttons[i].lib) a.lib = p.buttons[i].lib; }); S.saved = clone(r.profile); S.prof = clone(r.profile); }
    toast(r.written.length ? 'Saved to the mouse ✓' : 'Nothing to save');
    refreshBackups();
  } else toast(r.error, true);
  changed();
}

/* ------------------------------------------------------------------ events from Python */
const app = window.app = {
  on(kind, d) {
    if (kind === 'status') { S.status = d; renderStatus(); if (S.page === 'profiles') renderPage(); }
    else if (kind === 'profile') {
      const keepEdits = S.saved && dirty();
      const libs = S.prof ? S.prof.buttons.map(a => a.lib) : [];
      d.profile.buttons.forEach((a, i) => { if (a.kind === 'macro' && libs[i]) a.lib = libs[i]; });
      S.saved = clone(d.profile); S.warnings = d.warnings || [];
      if (!keepEdits) S.prof = clone(d.profile);
      else toast('Mouse settings re-read; your unsaved edits are kept');
      if (S.warnings.length) toast('Some settings on the mouse looked unusual and were reset: ' + S.warnings.join(', '), true);
      changed();
    } else if (kind === 'dpi') {
      if (S.prof) { S.prof.stage = d.stage; if (S.saved) S.saved.stage = d.stage; renderMouse(); if (S.page === 'perf') renderPage(); showHud(d.stage); }
    } else if (kind === 'background') {
      S.bg = d; if (S.page === 'combos') renderPage();
    } else if (kind === 'combo_rec') {
      if (S.cbEdit && S.cbRec) {
        const ed = S.cbEdit, seq = d.seq.slice(0, 6), pair = ed.action && ed.action.type === 'pair';
        ed.starter = d.starter;
        cbSetSeq(ed, pair && seq.length === 1 && WHEEL_TOKENS.includes(seq[0]) ? ['wheel_both'] : seq);   // a scroll keeps a pair
        cbRecord(false); renderPage();
      }
    } else if (kind === 'combo_error') {
      toast('Shortcut could not run: ' + d.error, true);
    } else if (kind === 'progress') {
      $('#progress i').style.width = Math.round(d.done / Math.max(1, d.total) * 100) + '%';
    }
  },
};

/* ------------------------------------------------------------------ boot */
async function start() {
  API = window.__MOCK_API__ || window.pywebview.api;
  const init = await API.init();
  S.mock = init.mock; S.factory = init.factory; S.buttons = init.buttons; S.hidden = init.hidden_buttons; S.media = init.media;
  S.pointer = init.pointer; S.presets = init.presets; S.settings = init.settings || {}; S.macros = S.settings.macros || [];
  S.bg = init.background;
  S.status = init.status;
  if (init.profile) { S.saved = clone(init.profile); S.prof = clone(init.profile); S.warnings = init.warnings; }
  else S.prof = clone(init.factory);
  renderTabs(); renderStatus(); renderMouse(); renderPage();
  $('#btnSave').onclick = save;
  $('#btnRevert').onclick = () => { if (S.saved) { S.prof = clone(S.saved); changed(); } };
  $('#btnMin').onclick = () => API.minimize();
  $('#btnClose').onclick = () => API.close();
  refreshBackups();
}
window.addEventListener('focus', () => { document.documentElement.classList.remove('idle'); });
window.addEventListener('blur', () => document.documentElement.classList.add('idle'));
if (window.__MOCK_API__) window.addEventListener('DOMContentLoaded', start);
else window.addEventListener('pywebviewready', start);
