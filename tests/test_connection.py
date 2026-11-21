import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.pop('TAIPAN_MOCK', None)
os.environ['TAIPAN_DATA'] = tempfile.mkdtemp(prefix='taipan-test-')
sys.path.insert(0, os.path.join(HERE, '..', 'app'))
import hidio
import m810 as M
import taipan

RECEIVER, WIRED = 0xFA7E, 0xFA7F


class World:
    def __init__(self):
        self.cable = False
        self.awake = True
        self.reads = 0
        self.checks = 0
        self.eeprom = bytearray(M.EEPROM_END)
        for parts in M.encode_sections(M.factory_profile()).values():
            for addr, data in parts:
                self.eeprom[addr:addr + len(data)] = data

    def devices(self):
        return [{'pid': p, 'kind': hidio.PIDS[p], 'version': 1, 'paths': {}} for p in
                ([RECEIVER, WIRED] if self.cable else [RECEIVER])]


class SimMouse:
    def __init__(self, world, dev):
        self.world, self.pid = world, dev['pid']

    def send(self, cmd, addr=0, data=b'', length=None, status=0, timeout=1.0, retries=3):
        w = self.world
        if self.pid == WIRED and not w.cable:
            raise hidio.DeviceError(f'No reply from the mouse to command 0x{cmd:02X}.')
        linked = self.pid == WIRED or (w.awake and not w.cable)
        n = len(data) if length is None else length
        out = bytearray(15)
        out[0], out[2], out[3], out[4] = cmd, addr >> 8 & 0xFF, addr & 0xFF, n
        if cmd == 0x03:
            w.checks += 1
            out[4], out[5] = 1, int(linked)
        elif cmd == 0x04:
            out[4], out[5], out[6] = 2, 5, int(w.cable)
        elif cmd == 0x08:
            if not linked:
                raise hidio.DeviceError(f'No reply from the mouse to command 0x{cmd:02X}.')
            w.reads += 1
            out[5:5 + n] = w.eeprom[addr:addr + n]
        return bytes(out)

    def close(self):
        pass


def setup(watch=None):
    world = World()
    hidio.enumerate_devices = world.devices
    hidio.Mouse = lambda dev, on_event=None: SimMouse(world, dev)
    taipan.redragon_running = lambda full=False: False
    d = taipan.Device(lambda kind, data: None)
    d.watch_motion = watch
    wait_for(d, lambda s: s['online'] and s['kind'] == '2.4G receiver' and s['battery'] == 50 and not s['charging'],
             'wireless at first')
    wait_for(d, lambda s: d.profile is not None and not s['busy'], 'settings read')
    return world, d


def wait_for(d, test, what, limit=12):
    t = time.time()
    while time.time() - t < limit:
        if test(d.status):
            return time.time() - t
        time.sleep(0.02)
    raise AssertionError(f'{what}: status is {d.status}')


def test_cable_in_and_out_with_windows_notice():
    world, d = setup()
    try:
        world.cable = True
        d.devices_changed()
        took = wait_for(d, lambda s: s['online'] and s['kind'] == 'wired' and s['charging'], 'switched to the cable')
        assert took < 1.0, took
        world.cable = False
        d.devices_changed()
        took = wait_for(d, lambda s: s['online'] and s['kind'] == '2.4G receiver' and not s['charging'],
                        'back on the receiver')
        assert took < 1.0, took
    finally:
        d.stop()


def test_cable_in_and_out_without_notice():
    world, d = setup()
    try:
        world.cable = True
        took = wait_for(d, lambda s: s['online'] and s['kind'] == 'wired' and s['charging'], 'switched to the cable')
        assert took < taipan.SCAN_AWAY + 1.5, took
        world.cable = False
        took = wait_for(d, lambda s: s['online'] and s['kind'] == '2.4G receiver', 'back on the receiver')
        assert took < 3.0, took
    finally:
        d.stop()


def test_waking_is_quick_and_reads_only_the_main_settings():
    world, d = setup()
    try:
        world.awake = False
        wait_for(d, lambda s: not s['online'], 'asleep')
        world.reads = 0
        world.awake = True
        took = wait_for(d, lambda s: s['online'], 'awake again')
        assert took < 0.6, took
        time.sleep(0.5)
        assert world.reads == 17, world.reads
        world.awake = False
        wait_for(d, lambda s: not s['online'], 'asleep')
        world.eeprom[0:2] = bytes([8, M.chk(8)])
        world.reads = 0
        world.awake = True
        wait_for(d, lambda s: s['online'], 'awake again')
        wait_for(d, lambda s: d.profile['polling'] == 125, 'the change was read')
        assert world.reads == 17 + 81, world.reads
    finally:
        d.stop()


def test_asleep_nothing_runs_until_the_mouse_moves():
    watching = []
    world, d = setup(watch=watching.append)
    try:
        world.awake = False
        wait_for(d, lambda s: not s['online'], 'asleep')
        time.sleep(0.3)
        assert watching == [True], watching
        world.checks = 0
        time.sleep(3)
        assert world.checks == 0, world.checks
        world.awake = True
        d.motion()
        took = wait_for(d, lambda s: s['online'], 'awake again')
        assert took < 0.2, took
        time.sleep(0.2)
        assert watching == [True, False], watching
    finally:
        d.stop()

if __name__ == '__main__':
    for n, f in list(globals().items()):
        if n.startswith('test_'):
            t = time.time()
            f()
            print('ok', n, f'({time.time() - t:.1f} s)')
