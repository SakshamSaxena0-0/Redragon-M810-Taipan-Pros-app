"""Taipan Studio - a replacement for the Redragon M810 Taipan Pro configuration software.

The window is an HTML page (app/ui) shown with pywebview (Microsoft Edge WebView2). This file is the bridge:
the page calls Api methods through window.pywebview.api.<name>(...), and Python pushes device events into the
page with window.evaluate_js('app.<handler>(...)').

TAIPAN_MOCK=1 runs against fakemouse.FakeMouse instead of the real receiver; TAIPAN_DATA=<folder> keeps settings
somewhere other than %APPDATA%\TaipanStudio (each data folder is its own single instance). --tray starts hidden;
--quit asks the copy that is running to exit (a rebuild can't replace the exe while it runs).

Closing the window hides it to the notification area (when "keep running in the tray" is on), so mouse-button
combos (combos.py) keep working; Quit in the tray menu exits.
"""
import ctypes
import ctypes.wintypes as W
import hashlib
import json
import os
import re
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import combos  # noqa: E402
import icons  # noqa: E402
import hidio  # noqa: E402
import m810 as M  # noqa: E402

APP_NAME = 'Taipan Studio'
APP_VERSION = '1.0.0'
MOCK = os.environ.get('TAIPAN_MOCK') == '1'
DATA_DIR = os.environ.get('TAIPAN_DATA') or os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'),
                                                         'TaipanStudio')
INSTANCE = 'TaipanStudio-' + hashlib.md5(os.path.abspath(DATA_DIR).lower().encode()).hexdigest()[:8]
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)


def resource(*parts):
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def _path(*parts):
    p = os.path.join(DATA_DIR, *parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


SECTION_NAMES = {'polling': 'polling rate', 'dpi': 'DPI stages', 'dpi_color': 'DPI colours', 'dpi_led': 'DPI light',
                 'main_led': 'lighting', 'buttons': 'buttons and macros'}


def _log(text, before=None, after=None, wanted=None, names=()):
    """Append to save-log.txt in the data folder: what was written and, for each section, the bytes the mouse had
    before, what it should hold and what it read back. Only failures are logged; the file stays small."""
    try:
        path = _path('save-log.txt')
        if os.path.exists(path) and os.path.getsize(path) > 512 * 1024:
            os.replace(path, path + '.old')
        lines = [time.strftime('%Y-%m-%d %H:%M:%S ') + text]
        for name in names:
            for addr, data in (wanted or {}).get(name, []):
                end = addr + len(data)
                lines += [f'  {name} 0x{addr:04X}', f'    before {bytes(before[addr:end]).hex(" ")}',
                          f'    wanted {data.hex(" ")}', f'    got    {bytes(after[addr:end]).hex(" ")}']
        with open(path, 'a', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
    except OSError:
        pass


def _load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def _safe_name(name):
    return re.sub(r'[^\w\- ]+', '', name).strip()[:40] or 'Preset'


# ------------------------------------------------------------------------------------- Windows pointer settings
SPI_GETMOUSE, SPI_SETMOUSE = 0x03, 0x04
SPI_SETDOUBLECLICKTIME = 0x20
SPI_GETMOUSESPEED, SPI_SETMOUSESPEED = 0x70, 0x71
SPI_GETWHEELSCROLLLINES, SPI_SETWHEELSCROLLLINES = 0x68, 0x69
SPIF = 0x01 | 0x02
user32.SystemParametersInfoW.argtypes = [W.UINT, W.UINT, ctypes.c_void_p, W.UINT]


def pointer_settings():
    speed, lines, acc = W.UINT(), W.UINT(), (ctypes.c_int * 3)()
    user32.SystemParametersInfoW(SPI_GETMOUSESPEED, 0, ctypes.byref(speed), 0)
    user32.SystemParametersInfoW(SPI_GETWHEELSCROLLLINES, 0, ctypes.byref(lines), 0)
    user32.SystemParametersInfoW(SPI_GETMOUSE, 0, acc, 0)
    return {'speed': speed.value, 'scroll': lines.value, 'precision': acc[2] != 0,
            'double_click': user32.GetDoubleClickTime()}


def set_pointer_settings(s):
    if 'speed' in s:
        user32.SystemParametersInfoW(SPI_SETMOUSESPEED, 0, ctypes.c_void_p(max(1, min(20, int(s['speed'])))), SPIF)
    if 'scroll' in s:
        user32.SystemParametersInfoW(SPI_SETWHEELSCROLLLINES, max(1, min(100, int(s['scroll']))), None, SPIF)
    if 'precision' in s:
        acc = (ctypes.c_int * 3)(*((6, 10, 1) if s['precision'] else (0, 0, 0)))
        user32.SystemParametersInfoW(SPI_SETMOUSE, 0, acc, SPIF)
    if 'double_click' in s:
        user32.SystemParametersInfoW(SPI_SETDOUBLECLICKTIME, max(200, min(900, int(s['double_click']))), None, SPIF)
    return pointer_settings()


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', W.DWORD), ('cntUsage', W.DWORD), ('th32ProcessID', W.DWORD),
                ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', W.DWORD), ('cntThreads', W.DWORD),
                ('th32ParentProcessID', W.DWORD), ('pcPriClassBase', W.LONG), ('dwFlags', W.DWORD),
                ('szExeFile', W.WCHAR * 260)]


kernel32.CreateToolhelp32Snapshot.restype = W.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
kernel32.Process32FirstW.argtypes = kernel32.Process32NextW.argtypes = [W.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.CloseHandle.argtypes = [W.HANDLE]


kernel32.OpenMutexW.restype = W.HANDLE
kernel32.OpenMutexW.argtypes = [W.DWORD, W.BOOL, W.LPCWSTR]
user32.FindWindowW.restype = W.HWND
user32.FindWindowW.argtypes = [W.LPCWSTR, W.LPCWSTR]


def redragon_running(full=False):
    """True when the original OemDrv.exe is open; two apps on one receiver would steal each other's replies.
    The quick check looks for OemDrv's single-instance mutex (the default name in OemDrv.exe) or its window title
    from Cfg.ini, about 20 microseconds. full=True also walks a process snapshot (~14 ms of CPU with 300
    processes), which the device thread does once a minute in case another OemDrv build names things differently."""
    h = kernel32.OpenMutexW(0x00100000, False, 'MUTEX_COMPX_WIRELESS')     # SYNCHRONIZE
    if h:
        kernel32.CloseHandle(h)
        return True
    if user32.FindWindowW(None, 'Redragon M810RGB-PRO'):
        return True
    if not full:
        return False
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)        # TH32CS_SNAPPROCESS
    if snap in (None, W.HANDLE(-1).value):
        return False
    try:
        e = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
        ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == 'oemdrv.exe':
                return True
            ok = kernel32.Process32NextW(snap, ctypes.byref(e))
        return False
    finally:
        kernel32.CloseHandle(snap)


def autostart_command():
    """What the Run key holds to start the built exe hidden in the tray; None when running from source."""
    return f'"{sys.executable}" --tray' if getattr(sys, 'frozen', False) else None


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, APP_NAME)[0] == autostart_command()
    except OSError:
        return False


def set_autostart(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on and autostart_command():
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass
    return autostart_enabled()


# ------------------------------------------------------------------------------------- device worker
class Device:
    """Owns the connection. A background thread keeps the receiver open, notices the mouse waking up,
    and reads its settings; the Api calls into it for saves, backups and restores."""

    def __init__(self, push):
        self.push = push
        self.lock = threading.RLock()
        self.mouse = None
        self.dev = None
        self.status = {'receiver': False, 'online': False, 'kind': '', 'busy': '', 'error': '', 'conflict': False}
        self.image = None             # EEPROM image as last read / written
        self.sections = None          # encode_sections() of what is on the mouse now
        self.profile = None
        self.warnings = []
        self._wake = threading.Event()
        self._quit = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    # -- plumbing
    def _set(self, **kw):
        self.status.update(kw)
        self.push('status', self.status)

    def _event(self, payload):
        flags = payload[5]
        if flags & 0x01:
            level = payload[6] & 0x0F
            if self.profile and level < self.profile['stage_count']:
                self.profile['stage'] = level
            self.push('dpi', {'stage': level})
        if flags & 0x06:            # polling switch or mouse reset: settings may have changed on the device
            self._reload = True
            self._wake.set()
        if flags & 0xE0:
            self.push('led', {'dpi': bool(payload[7] & 0x20), 'main': bool(payload[7] & 0x80)})

    def _open(self):
        if MOCK:
            import fakemouse
            m = fakemouse.FakeMouse(on_event=self._event)
            return m, m.info
        devs = hidio.enumerate_devices()
        if not devs:
            return None, None
        dev = devs[0]
        return hidio.Mouse(dev, on_event=self._event), dev

    def _close(self):
        if self.mouse:
            try:
                M.M810(self.mouse).bye()
            except Exception:
                pass
            self.mouse.close()
        self.mouse = None

    def _run(self):
        self._reload = True
        last_ping = last_conflict = last_full = 0
        while not self._quit.is_set():
            try:
                if not MOCK and time.time() - last_conflict > 5:
                    last_conflict = time.time()
                    full = last_conflict - last_full > 60
                    if full:
                        last_full = last_conflict
                    conflict = redragon_running(full)
                    if conflict != self.status['conflict']:
                        self._set(conflict=conflict)
                    if conflict:
                        self._close()
                        self._wake.wait(3.0)
                        self._wake.clear()
                        continue
                with self.lock:
                    if not self.mouse:
                        self.mouse, self.dev = self._open()
                        if not self.mouse:
                            self._set(receiver=False, online=False, kind='')
                            self._wake.wait(2.0)
                            self._wake.clear()
                            continue
                        dev = M.M810(self.mouse)
                        online = dev.hello()
                        self._set(receiver=True, online=online, kind=self.dev['kind'], error='')
                        self._reload = True
                    dev = M.M810(self.mouse)
                    online = dev.online()
                    if online != self.status['online']:
                        self._set(online=online)
                        if online:
                            self._reload = True
                    if online and self._reload:
                        self._reload = False
                        self._read_all(dev)
                    if time.time() - last_ping > 30:
                        dev.ping()
                        last_ping = time.time()
            except hidio.DeviceError as e:
                self._set(online=False, error=str(e))
                if 'disconnected' in str(e) or 'open' in str(e):
                    self._close()
            except Exception as e:
                traceback.print_exc()
                self._set(error=str(e))
            self._wake.wait(1.5 if self.status['online'] else 2.5)
            self._wake.clear()
        self._close()

    def _read_all(self, dev):
        self._set(busy='Reading your mouse…')
        try:
            img = dev.read_settings(lambda a, b: self.push('progress', {'done': a, 'total': b}))
            self.image = img
            self.profile, self.warnings = M.decode_profile(img)
            self.sections = M.encode_sections(self.profile) if not self.warnings else None
            self.push('profile', {'profile': self.profile, 'warnings': self.warnings})
        finally:
            self._set(busy='')

    def stop(self):
        self._quit.set()
        self._wake.set()

    def reload(self):
        self._reload = True
        self._wake.set()

    # -- operations used by the Api (each holds the lock so the poller pauses)
    def _need_online(self):
        if not self.mouse or not self.status['online']:
            raise hidio.DeviceError('The mouse is asleep or out of range. Move it, then try again.')
        return M.M810(self.mouse)

    def save(self, profile):
        with self.lock:
            dev = self._need_online()
            new = M.encode_sections(profile)
            if not os.listdir(os.path.dirname(_path('backups', 'x'))):
                self._backup(dev, auto=True)
            self._set(busy='Saving to mouse…')
            try:
                # compare with what the mouse holds now, not when the app last read it: its own buttons (polling,
                # DPI) or a reset can change it in between, and those sections must be written again too
                before = dev.read_settings()
                cur, warn = M.decode_profile(before)
                names = M.diff_sections(M.encode_sections(cur) if not warn else None, new)
                if not names:
                    self.image, self.profile, self.warnings, self.sections = before, cur, [], new
                    return {'written': [], 'profile': cur}
                dev.apply(new, names, lambda a, b: self.push('progress', {'done': a, 'total': b}))
                img = dev.read_settings()
                got, warn = M.decode_profile(img)
                self.image, self.profile, self.warnings = img, got, warn
                self.sections = M.encode_sections(got) if not warn else None
                wanted = M.encode_sections(dict(profile, stage=got['stage']))
                lost = list(wanted) if warn else M.diff_sections(self.sections, wanted)
                if lost:
                    _log(f'save: wrote {names}; read back differs in {lost}', before, img, wanted, lost)
                    self.push('profile', {'profile': got, 'warnings': warn})      # show what the mouse really has
                    raise hidio.DeviceError('The mouse did not keep: ' + ', '.join(SECTION_NAMES.get(n, n) for n in lost)
                                            + '. Press Save to write it again.')
                return {'written': names, 'profile': got}
            finally:
                self._set(busy='')

    def _backup(self, dev, auto=False):
        self._set(busy='Backing up the mouse memory (first save only)…' if auto else 'Backing up the mouse memory…')
        try:
            img = dev.backup(lambda a, b: self.push('progress', {'done': a, 'total': b}))
        finally:
            self._set(busy='')
        name = time.strftime('m810-%Y%m%d-%H%M%S') + ('-auto' if auto else '') + '.bin'
        path = _path('backups', name)
        with open(path, 'wb') as f:
            f.write(img)
        return path

    def backup(self):
        with self.lock:
            return self._backup(self._need_online())

    def restore(self, data):
        if len(data) != M.EEPROM_END:
            raise ValueError('That is not a Taipan Studio backup (wrong size).')
        _, warn = M.decode_profile(bytearray(data))
        if len(warn) > 3:
            raise ValueError('That backup does not look like M810 settings.')
        with self.lock:
            dev = self._need_online()
            self._set(busy='Restoring backup…')
            try:
                dev.restore(data, lambda a, b: self.push('progress', {'done': a, 'total': b}))
            finally:
                self._set(busy='')
            self._read_all(dev)


# ------------------------------------------------------------------------------------- API for the page
class Api:
    def __init__(self):
        self._window = None
        self._tray = None
        self._quitting = False
        self._settings = _load_json(_path('settings.json'), {})
        self._dev = Device(self._push)
        self._combos = combos.Combos(on_record=self._on_record, on_error=self._on_combo_error)
        self._combos.configure(self._combo_cfg())

    def _push(self, kind, data):
        w = self._window
        if w:
            try:
                w.evaluate_js(f'window.app && app.on({json.dumps(kind)}, {json.dumps(data)})')
            except Exception:
                pass

    def _err(self, e):
        return {'ok': False, 'error': str(e) or e.__class__.__name__}

    # -- startup
    def init(self):
        d = self._dev
        return {'version': APP_VERSION, 'mock': MOCK, 'status': d.status, 'profile': d.profile,
                'warnings': d.warnings, 'factory': M.factory_profile(), 'pointer': pointer_settings(),
                'presets': self.presets(), 'settings': self._settings,
                'buttons': [{'slot': s, 'name': n} for s, n, _ in M.BUTTONS],
                'hidden_buttons': [{'slot': s, 'name': n} for s, n, _ in M.HIDDEN_BUTTONS],
                'media': list(M.MEDIA), 'dpi_values': [M.DPI_MIN, M.DPI_MAX], 'background': self.background()}

    def set_setting(self, key, value):
        self._settings[key] = value
        _save_json(_path('settings.json'), self._settings)

    # -- device
    def reload(self):
        self._dev.reload()

    def save(self, profile):
        try:
            return dict(self._dev.save(profile), ok=True)
        except (hidio.DeviceError, ValueError, KeyError) as e:
            return self._err(e)

    def dpi_actual(self, x, y):
        return M.dpi_actual(x, y)

    def backup(self):
        try:
            return {'ok': True, 'path': self._dev.backup()}
        except hidio.DeviceError as e:
            return self._err(e)

    def backups(self):
        folder = os.path.dirname(_path('backups', 'x'))
        out = []
        for n in sorted(os.listdir(folder), reverse=True):
            if n.endswith('.bin'):
                p = os.path.join(folder, n)
                out.append({'name': n, 'path': p, 'time': os.path.getmtime(p)})
        return out

    def restore(self, path=None):
        try:
            if not path:
                import webview
                r = self._window.create_file_dialog(webview.FileDialog.OPEN, directory=os.path.dirname(
                    _path('backups', 'x')), file_types=('Mouse backup (*.bin)',))
                if not r:
                    return {'ok': False, 'cancelled': True}
                path = r[0]
            with open(path, 'rb') as f:
                self._dev.restore(f.read())
            return {'ok': True}
        except (hidio.DeviceError, ValueError, OSError) as e:
            return self._err(e)

    def open_backups(self):
        os.startfile(os.path.dirname(_path('backups', 'x')))

    # -- presets kept on this PC
    def presets(self):
        folder = os.path.dirname(_path('presets', 'x'))
        out = []
        for n in sorted(os.listdir(folder)):
            if n.endswith('.json'):
                d = _load_json(os.path.join(folder, n), None)
                if d and 'profile' in d:
                    out.append({'name': d.get('name', n[:-5]), 'file': n, 'profile': d['profile']})
        return out

    def save_preset(self, name, profile):
        name = _safe_name(name)
        _save_json(_path('presets', name + '.json'), {'name': name, 'app': APP_NAME, 'profile': profile})
        return self.presets()

    def delete_preset(self, file):
        try:
            os.remove(_path('presets', os.path.basename(file)))
        except OSError:
            pass
        return self.presets()

    def export_profile(self, profile):
        import webview
        r = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename='M810 profile.json',
                                            file_types=('Profile (*.json)',))
        if not r:
            return {'ok': False, 'cancelled': True}
        path = r if isinstance(r, str) else r[0]
        _save_json(path, {'name': os.path.splitext(os.path.basename(path))[0], 'app': APP_NAME, 'profile': profile})
        return {'ok': True, 'path': path}

    def import_profile(self):
        import webview
        r = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=('Profile (*.json)',))
        if not r:
            return {'ok': False, 'cancelled': True}
        d = _load_json(r[0], None)
        if not d or 'profile' not in d:
            return self._err('That file is not a Taipan Studio profile.')
        return {'ok': True, 'profile': d['profile'], 'name': d.get('name', '')}

    # -- Windows pointer settings
    def pointer(self):
        return pointer_settings()

    def set_pointer(self, s):
        return set_pointer_settings(s)

    # -- combos (combos.py) and running in the tray
    def _combo_cfg(self):
        return self._settings.get('combos') or json.loads(json.dumps(combos.DEFAULT))

    def background(self):
        hook = self._combos.hook
        return {'combos': self._combo_cfg(), 'tray': self._settings.get('tray', True),
                'autostart': autostart_enabled(), 'can_autostart': autostart_command() is not None,
                'native': hook.native, 'hooked': bool(hook.handle)}

    def set_combos(self, cfg):
        self._settings['combos'] = cfg
        _save_json(_path('settings.json'), self._settings)
        self._combos.configure(cfg)
        self._sync_hook()
        return self.background()

    def record_combo(self, on):
        self._combos.engine.recording = bool(on)
        self._sync_hook()

    def try_action(self, action):
        self._combos.runner.run(action)

    def running_apps(self):
        return combos.window_apps()

    def target_icon(self, path):
        return icons.icon_data_url(path)

    def pick_target(self):
        import webview
        r = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=(
            'Apps and shortcuts (*.exe;*.lnk;*.bat;*.cmd)', 'All files (*.*)'))
        return r[0] if r else None

    def set_tray(self, on):
        self.set_setting('tray', bool(on))
        return self.background()

    def set_autostart(self, on):
        set_autostart(bool(on))
        return self.background()

    def _on_record(self, starter, seq):
        self._push('combo_rec', {'starter': starter, 'seq': seq})

    def _on_combo_error(self, action, e):
        self._push('combo_error', {'error': str(e) or e.__class__.__name__})

    def _sync_hook(self):
        """The hook is in only while a combo is on or a gesture is being recorded; otherwise mouse input never
        reaches this process at all. The change runs on the tray thread, which owns the hook."""
        if not self._tray:
            return
        engine, hook = self._combos.engine, self._combos.hook

        def apply():
            if engine.active and not hook.handle:
                hook.install()
            elif not engine.active and hook.handle:
                hook.uninstall()
        self._tray.call(apply)
        self._tray.set_tip('Taipan Studio · shortcuts ' + ('on' if self._combo_cfg().get('enabled') else 'off'))

    def _refresh_hook(self, reason):
        if self._combos.engine.active and (reason != 'timer' or not self._combos.engine.armed):
            self._combos.hook.install()

    def _toggle_combos(self):
        cfg = self._combo_cfg()
        cfg['enabled'] = not cfg.get('enabled')
        self._push('background', self.set_combos(cfg))

    def start_background(self):
        import tray
        self._tray = tray.Tray(INSTANCE, resource('assets', 'icon.ico'), on_open=self.show, on_quit=self.quit,
                               on_combos=self._toggle_combos, combos_on=lambda: self._combo_cfg().get('enabled'),
                               on_refresh=self._refresh_hook, on_ready=self._sync_hook)

    # -- window
    def minimize(self):
        self._window.minimize()

    def show(self):
        if self._window:
            self._window.show()
            self._window.restore()

    def close(self):
        """The window's close button: hide to the tray when that is on, otherwise quit."""
        if self._tray and self._settings.get('tray', True):
            self._window.hide()
            if not self._settings.get('tray_hint'):
                self._tray.balloon('Still running in the tray so your shortcuts keep working. '
                                   'Right-click the icon to quit.')
                self.set_setting('tray_hint', True)
        else:
            self.quit()

    def quit(self):
        self._quitting = True
        self._dev.stop()
        if self._tray:
            self._tray.stop()
        self._window.destroy()

    def _on_closing(self):
        """Alt+F4 and the taskbar's Close act like the close button. Returning False keeps the window."""
        if self._quitting or not (self._tray and self._settings.get('tray', True)):
            self._quitting = True
            return True
        threading.Thread(target=self.close, daemon=True).start()
        return False

    def demo_dpi(self, level):
        if MOCK and self._dev.mouse:
            self._dev.mouse.press_dpi(int(level))


def main():
    import tray
    if tray.already_running(INSTANCE, quit='--quit' in sys.argv) or '--quit' in sys.argv:
        return
    import webview
    api = Api()
    window = webview.create_window(APP_NAME, resource('ui', 'index.html'), js_api=api, width=1240, height=800,
                                   min_size=(1020, 680), frameless=True, easy_drag=False,
                                   background_color='#000000', hidden='--tray' in sys.argv)
    api._window = window
    window.events.closing += api._on_closing
    window.events.closed += lambda: (api._dev.stop(), api._tray and api._tray.stop())
    api.start_background()
    icon = resource('assets', 'icon.ico')
    webview.start(gui='edgechromium', icon=icon if os.path.exists(icon) else None,
                  debug=os.environ.get('TAIPAN_DEBUG') == '1')


if __name__ == '__main__':
    main()
