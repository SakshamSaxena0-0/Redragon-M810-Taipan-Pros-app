import os
import threading
import time

import m810 as M


class FakeMouse:
    info = {'pid': 0xFA7E, 'kind': 'demo mouse', 'version': 0x0331, 'paths': {}}

    def __init__(self, on_event=None):
        self.on_event = on_event
        self.eeprom = bytearray(M.EEPROM_END)
        for parts in M.encode_sections(M.factory_profile()).values():
            for addr, data in parts:
                self.eeprom[addr:addr + len(data)] = data
        self.eeprom[4], self.eeprom[5] = 0, M.chk(0)
        self.writes = 0
        self.battery = 7
        self.charging = os.environ.get('TAIPAN_MOCK_CHARGING') == '1'
        self._stop = threading.Event()

    def send(self, cmd, addr=0, data=b'', length=None, status=0, timeout=1.0, retries=3):
        time.sleep(0.004)
        n = len(data) if length is None else length
        out = bytearray(15)
        out[0], out[2], out[3], out[4] = cmd, addr >> 8 & 0xFF, addr & 0xFF, n
        if cmd == 0x03:
            out[4], out[5] = 1, 1
        elif cmd == 0x04:
            out[4], out[5], out[6] = 2, self.battery, int(self.charging)
        elif cmd == 0x06:
            out[4], out[5], out[6] = 2, 0x01, 0x17
        elif cmd == 0x08:
            out[5:5 + n] = self.eeprom[addr:addr + n]
        elif cmd == 0x07:
            self.eeprom[addr:addr + n] = data[:n]
            self.writes += 1
        elif cmd == 0x09:
            self.__init__(self.on_event)
        return bytes(out)

    def press_dpi(self, level):
        self.eeprom[4], self.eeprom[5] = level, M.chk(level)
        if self.on_event:
            self.on_event(bytes([0x0A, 0, 0, 0, 3, 0x01, level, 0, 0, 0, 0, 0, 0, 0, 0]))

    def close(self):
        self._stop.set()
