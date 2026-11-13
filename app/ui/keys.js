/* USB HID keyboard usages (what the mouse stores) <-> browser KeyboardEvent.code + display names. */
'use strict';
const HID = (() => {
  const byCode = {};
  const names = {};
  const add = (code, usage, name) => { byCode[code] = usage; names[usage] = name; };
  for (let i = 0; i < 26; i++) add('Key' + String.fromCharCode(65 + i), 4 + i, String.fromCharCode(65 + i));
  for (let i = 1; i <= 9; i++) add('Digit' + i, 29 + i, String(i));
  add('Digit0', 39, '0');
  [['Enter', 40, 'Enter'], ['Escape', 41, 'Esc'], ['Backspace', 42, 'Backspace'], ['Tab', 43, 'Tab'],
   ['Space', 44, 'Space'], ['Minus', 45, '-'], ['Equal', 46, '='], ['BracketLeft', 47, '['], ['BracketRight', 48, ']'],
   ['Backslash', 49, '\\'], ['Semicolon', 51, ';'], ['Quote', 52, "'"], ['Backquote', 53, '`'], ['Comma', 54, ','],
   ['Period', 55, '.'], ['Slash', 56, '/'], ['CapsLock', 57, 'Caps Lock']].forEach(a => add(...a));
  for (let i = 1; i <= 12; i++) add('F' + i, 57 + i, 'F' + i);
  [['PrintScreen', 70, 'Print Screen'], ['ScrollLock', 71, 'Scroll Lock'], ['Pause', 72, 'Pause'], ['Insert', 73, 'Insert'],
   ['Home', 74, 'Home'], ['PageUp', 75, 'Page Up'], ['Delete', 76, 'Delete'], ['End', 77, 'End'], ['PageDown', 78, 'Page Down'],
   ['ArrowRight', 79, '→'], ['ArrowLeft', 80, '←'], ['ArrowDown', 81, '↓'], ['ArrowUp', 82, '↑'], ['NumLock', 83, 'Num Lock'],
   ['NumpadDivide', 84, 'Num /'], ['NumpadMultiply', 85, 'Num *'], ['NumpadSubtract', 86, 'Num -'], ['NumpadAdd', 87, 'Num +'],
   ['NumpadEnter', 88, 'Num Enter']].forEach(a => add(...a));
  for (let i = 1; i <= 9; i++) add('Numpad' + i, 88 + i, 'Num ' + i);
  add('Numpad0', 98, 'Num 0'); add('NumpadDecimal', 99, 'Num .'); add('IntlBackslash', 100, '\\ (ISO)');
  add('ContextMenu', 101, 'Menu');
  for (let i = 13; i <= 24; i++) add('F' + i, 91 + i, 'F' + i);   // F13-F24: 104-115, not on most keyboards
  const MODS = { ControlLeft: 'ctrl', ShiftLeft: 'shift', AltLeft: 'alt', MetaLeft: 'win',
                 ControlRight: 'rctrl', ShiftRight: 'rshift', AltRight: 'ralt', MetaRight: 'rwin' };
  const MOD_NAMES = { ctrl: 'Ctrl', shift: 'Shift', alt: 'Alt', win: 'Win', rctrl: 'Right Ctrl', rshift: 'Right Shift',
                      ralt: 'Right Alt', rwin: 'Right Win' };
  return {
    usage: code => byCode[code],
    name: u => names[u] || ('Key 0x' + Number(u).toString(16).toUpperCase()),
    mod: code => MODS[code],
    modName: m => MOD_NAMES[m] || m,
    extra: [...Array(12)].map((_, i) => ({ usage: 104 + i, name: 'F' + (13 + i) })),
  };
})();
