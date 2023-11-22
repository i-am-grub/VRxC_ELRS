import logging
import hashlib
import serial
import time
import copy

from threading import Thread, Lock
import queue
import serial.tools.list_ports
import gevent

import RHUtils
from RHRace import RaceStatus
from VRxControl import VRxController

from plugins.VRxC_ELRS.constants import HARDWARE_SETTINGS

logger = logging.getLogger(__name__)

class elrsBackpack(VRxController):
    
    _queue_lock = Lock()
    _delay_lock = Lock()
    _repeat_count = 0
    _send_delay = 0.05
    
    _heat_name = None
    _heat_data = {}
    _finished_pilots = []
    _queue_full = False

    def __init__(self, name, label, rhapi):
        super().__init__(name, label)
        self._rhapi = rhapi

        self._backpack_queue = queue.Queue(maxsize=200)
        Thread(target=self.backpack_connector, daemon=True).start()

    def registerHandlers(self, args):
        args['register_fn'](self)

    def setOptions(self, _args = None):

        self._queue_lock.acquire()

        if self._rhapi.db.option('_heat_name') == "1":
            self._heat_name = True
        else:
            self._heat_name = False
        if self._rhapi.db.option('_position_mode') == "1":
            self._position_mode = True
        else:
            self._position_mode = False
        if self._rhapi.db.option('_gap_mode') == "1":
            self._gap_mode = True
        else:
            self._gap_mode = False
        if self._rhapi.db.option('_results_mode') == "1":
            self._results_mode = True
        else:
            self._results_mode = False

        self._racestage_message = self._rhapi.db.option('_racestage_message')
        self._racestart_message = self._rhapi.db.option('_racestart_message')
        self._pilotdone_message = self._rhapi.db.option('_pilotdone_message')
        self._racefinish_message = self._rhapi.db.option('_racefinish_message')
        self._racestop_message = self._rhapi.db.option('_racestop_message')
        self._leader_message = self._rhapi.db.option('_leader_message')

        self._racestart_uptime = self._rhapi.db.option('_racestart_uptime') * 0.1
        self._finish_uptime = self._rhapi.db.option('_finish_uptime') * 0.1
        self._results_uptime = self._rhapi.db.option('_results_uptime') * 0.1
        self._announcement_uptime = self._rhapi.db.option('_announcement_uptime') * 0.1

        self._status_row = self._rhapi.db.option('_status_row')
        self._currentlap_row = self._rhapi.db.option('_currentlap_row')
        self._lapresults_row = self._rhapi.db.option('_lapresults_row')
        self._announcement_row = self._rhapi.db.option('_announcement_row')

        self._repeat_count = self._rhapi.db.option('_bp_repeat')
        with self._delay_lock:
            self._send_delay = self._rhapi.db.option('_bp_delay') * 0.001

        self._queue_lock.release()

    def start_race(self):
        start_race_args = {'start_time_s' : 10}
        if self._rhapi.race.status == RaceStatus.READY:
            self._rhapi.race.stage(start_race_args)

    def stop_race(self):
        self._rhapi.race.stop()

    #
    # Backpack communications
    #

    def backpack_connector(self):
        config_messages     = [0x09, 0x0C, 0xB5]
        version_message     = [36, 88, 60, 0, 16, 0, 0, 0, 174]
        version_response    = [36, 88, 62, 0]
        start_message       = [36, 88, 60, 0, 5, 3, 3, 0, 1, 0, 0, 74]
        stop_message        = [36, 88, 60, 0, 5, 3, 3, 0, 0, 5, 0, 238]
        
        logger.info("Attempting to find backpack")
        
        ports = list(serial.tools.list_ports.comports())
        s = serial.Serial(baudrate=460800,
                        bytesize=8, parity='N', stopbits=1,
                        timeout=0.01, xonxoff=0, rtscts=0,
                        write_timeout=0.01)
        
        # Search for connected backpack
        for port in ports:
            s.port = port.device
            try:
                s.open()
            except:
                continue

            try:
                s.write(version_message)
            except:
                logger.error('Failed to write to open serial device. Attempting to connect to new device...')
                s.close()
                continue

            response = s.read(len(version_response))
            if list(response) == version_response:
                logger.info(f"Connected to backpack on {port.device}")
                backpack_connected = True
                break
            else:
                s.close()
        else:
            logger.warning("Could not find connected backpack. Ending connector thread.")
            backpack_connected = False

        error_count = 0

        while backpack_connected:

            self._delay_lock.acquire()
            delay = copy.copy(self._send_delay)
            self._delay_lock.release()

            # Handle backpack comms 
            while not self._backpack_queue.empty():
                message = self._backpack_queue.get()

                if message[4] not in config_messages:
                    time.sleep(delay)
                
                try:
                    s.write(message)
                except:
                    error_count += 1
                    if error_count > 5:
                        backpack_connected = False
                        logger.error('Failed to write to backpack. Ending connector thread')
                        s.close()
                else:
                    error_count = 0

            response = s.read(12)

            # Send message to thread to start race
            if not response:
                pass
            elif list(response) == start_message:
                gevent.spawn(self.start_race)
                response = None
            elif list(response) == stop_message:
                gevent.spawn(self.stop_race)
                response = None
             
            time.sleep(0.01)

    #
    # Backpack message generation
    #

    def hash_phrase(self, bindphrase:str) -> list:
        bindingPhraseHash = [x for x in hashlib.md5(("-DMY_BINDING_PHRASE=\"" + bindphrase + "\"").encode()).digest()[0:6]]
        if (bindingPhraseHash[0] % 2) == 1:
            bindingPhraseHash[0] -= 0x01
        return bindingPhraseHash

    def queue_add(self, msp):
        try:
            self._backpack_queue.put(msp, block=False)
        except queue.Full:
            if not self._queue_full:
                self._queue_full = True
                message = 'ERROR: ELRS Backpack not responding. Please reboot the server to attempt to reconnect.'
                self._rhapi.ui.message_alert(self._rhapi.language.__(message))
        else:
            if self._queue_full:
                self._queue_full = False
                message = 'ELRS Backpack has start responding again.'
                self._rhapi.ui.message_notify(self._rhapi.language.__(message))

    def crc8_dvb_s2(self, crc, a):
        crc = crc ^ a
        for ii in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0xD5
            else:
                crc = crc << 1
        return crc & 0xFF
    
    def send_msp(self, body):
        config_messages = [0x09, 0x0C, 0xB5]

        crc = 0
        for x in body:
            crc = self.crc8_dvb_s2(crc, x)
        msp = [ord('$'),ord('X'),ord('<')]
        msp = msp + body
        msp.append(crc)
        self.queue_add(msp)
        if msp[4] not in config_messages:
            for count in range(self._repeat_count):
                self.queue_add(msp)
            
    def set_sendUID(self, bindingHash:list):
        msp = [0,0x00B5,0x00,7,0,1]
        for byte in bindingHash:
            msp.append(byte)
        self.send_msp(msp)

    def clear_sendUID(self):
        msp = [0,0x00B5,0x00,1,0,0]
        self.send_msp(msp)

    def send_clear(self):
        msp = [0,0x00B6,0x00,1,0,0x02]
        self.send_msp(msp)

    def send_msg(self, row, col, str):
        l = 4+len(str)
        msp = [0,0x00B6,0x00,l%256,int(l/256),0x03,row,col,0]
        for x in [*str]:
            msp.append(ord(x))
        self.send_msp(msp)

    def send_display(self):
        msp = [0,0x00B6,0x00,1,0,0x04]
        self.send_msp(msp)

    def centerOSD(self, stringlength, hardwaretype):
        offset = int(stringlength/2)
        if hardwaretype:
            col = int(HARDWARE_SETTINGS[hardwaretype]['row_size'] / 2) - offset
        else:
            col = 0
        return col
    
    def send_clear_row(self, row, hardwaretype):
        if hardwaretype:
            l = 4 + HARDWARE_SETTINGS[hardwaretype]['row_size']
        else:
            l = 0
        msp = [0,0x00B6,0x00,l%256,int(l/256),0x03,row,0,0]
        for x in range(HARDWARE_SETTINGS[hardwaretype]['row_size']):
            msp.append(0)
        self.send_msp(msp)

    def activate_bind(self, _args):
        message = "Activating backpack's bind mode..."
        self._rhapi.ui.message_notify(self._rhapi.language.__(message))
        msp = [0,128,3,1,0,66]
        with self._queue_lock:
            self.send_msp(msp)
    
    def activate_wifi(self, _args):
        message = "Turning on backpack's wifi..."
        self._rhapi.ui.message_notify(self._rhapi.language.__(message))
        msp = [0,128,3,1,0,87]
        with self._queue_lock:
            self.send_msp(msp)

    #
    # Connection Test
    #

    def test_osd(self, _args):

        def test():
            message = 'ROTORHAZARD'
            for row in range(HARDWARE_SETTINGS['hdzero']['column_size']):

                self._queue_lock.acquire()
                self.send_clear()
                start_col = self.centerOSD(len(message), 'hdzero')
                self.send_msg(row, start_col, message)    
                self.send_display()
                self._queue_lock.release()

                time.sleep(0.5)

                self._queue_lock.acquire()
                self.send_clear_row(row, 'hdzero')
                self.send_display()
                self._queue_lock.release()

            time.sleep(1)
            self._queue_lock.acquire()
            self.send_clear()
            self.send_display()
            self._queue_lock.release()

        Thread(target=test, daemon=True).start()

    #
    # VRxC Event Triggers
    #

    def onPilotAlter(self, args):
        pilot_id = args['pilot_id']
        self._queue_lock.acquire()

        if pilot_id in self._heat_data:
            pilot_settings = {}
            pilot_settings['hardware_type'] = self._rhapi.db.pilot_attribute_value(pilot_id, 'hardware_type')
            logger.info(f"Pilot {pilot_id}'s hardware set to {self._rhapi.db.pilot_attribute_value(pilot_id, 'hardware_type')}")

            bindphrase = self._rhapi.db.pilot_attribute_value(pilot_id, 'comm_elrs')
            if bindphrase:
                UID = self.hash_phrase(bindphrase)
                pilot_settings['UID'] = UID
            else:
                pilot_settings = None

            self._heat_data[pilot_id] = pilot_settings
            logger.info(f"Pilot {pilot_id}'s UID set to {UID}")

        self._queue_lock.release()

    def onHeatSet(self, args):

        heat_data = {}
        for slot in self._rhapi.db.slots_by_heat(args['heat_id']):
            if slot.pilot_id:
                hardware_type = self._rhapi.db.pilot_attribute_value(slot.pilot_id, 'hardware_type')
                bindphrase = self._rhapi.db.pilot_attribute_value(slot.pilot_id, 'comm_elrs')
                if hardware_type not in HARDWARE_SETTINGS:
                    heat_data[slot.pilot_id] = None
                    continue
                elif not bindphrase:
                    heat_data[slot.pilot_id] = None
                    continue

                pilot_settings = {}
                pilot_settings['hardware_type'] = hardware_type
                logger.info(f"Pilot {slot.pilot_id}'s hardware set to {self._rhapi.db.pilot_attribute_value(slot.pilot_id, 'hardware_type')}")

                UID = self.hash_phrase(bindphrase)
                pilot_settings['UID'] = UID
                
                heat_data[slot.pilot_id] = pilot_settings
                logger.info(f"Pilot {slot.pilot_id}'s UID set to {UID}")
        
        self._heat_data = heat_data

    def onRaceStage(self, args):
        # Set OSD options
        self.setOptions()

        # Setup heat if not done already
        with self._queue_lock:
            self._finished_pilots = []
            if not self._heat_data:
                self.onHeatSet(args)

        heat_data = self._rhapi.db.heat_by_id(args['heat_id'])
        if heat_data and self._heat_name:
            class_name = self._rhapi.db.raceclass_by_id(heat_data.class_id).name
            heat_name = heat_data.name
            round_trans = self._rhapi.__('Round')
            round_num = self._rhapi.db.heat_max_round(args['heat_id']) + 1
            if round_num > 1:
                race_name = f'x {class_name.upper()} | {heat_name.upper()} | {round_trans.upper()} {round_num} w'
            else:
                race_name = f'x {class_name.upper()} | {heat_name.upper()} w'

        # Send stage message to all pilots
        def arm(pilot_id):
            self._queue_lock.acquire()
            self.set_sendUID(self._heat_data[pilot_id]['UID'])
            self.send_clear()
            start_col1 = self.centerOSD(len(self._racestage_message), self._heat_data[pilot_id]['hardware_type'])
            self.send_msg(self._status_row, start_col1, self._racestage_message)
            if self._heat_name:
                start_col2 = self.centerOSD(len(race_name), self._heat_data[pilot_id]['hardware_type'])
                self.send_msg(self._announcement_row, start_col2, race_name)
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

        with self._queue_lock:
            for pilot_id in self._heat_data:
                if self._heat_data[pilot_id]:
                    Thread(target=arm, args=(pilot_id,), daemon=True).start()

    def onRaceStart(self, _args):
        
        def start(pilot_id):
            self._queue_lock.acquire()
            self.set_sendUID(self._heat_data[pilot_id]['UID'])
            self.send_clear()
            start_col = self.centerOSD(len(self._racestart_message), self._heat_data[pilot_id]['hardware_type'])
            self.send_msg(self._status_row, start_col, self._racestart_message)  
            self.send_display()
            self.clear_sendUID()
            delay = copy.copy(self._racestart_uptime)
            self._queue_lock.release()

            time.sleep(delay)

            self._queue_lock.acquire()
            self.set_sendUID(self._heat_data[pilot_id]['UID'])
            self.send_clear_row(self._status_row, self._heat_data[pilot_id]['hardware_type'])
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

        with self._queue_lock:
            for pilot_id in self._heat_data:
                if self._heat_data[pilot_id]:
                    thread1 = Thread(target=start, args=(pilot_id,), daemon=True)
                    thread1.start()

    def onRaceFinish(self, _args):
        
        def start(pilot_id):
            self._queue_lock.acquire()
            self.set_sendUID(self._heat_data[pilot_id]['UID'])
            self.send_clear_row(self._status_row, self._heat_data[pilot_id]['hardware_type'])
            start_col = self.centerOSD(len(self._racefinish_message), self._heat_data[pilot_id]['hardware_type'])
            self.send_msg(self._status_row, start_col, self._racefinish_message)  
            self.send_display()
            self.clear_sendUID()
            delay = copy.copy(self._finish_uptime)
            self._queue_lock.release()

            time.sleep(delay)

            self._queue_lock.acquire()
            self.set_sendUID(self._heat_data[pilot_id]['UID'])
            self.send_clear_row(self._status_row, self._heat_data[pilot_id]['hardware_type'])
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

        with self._queue_lock:
            for pilot_id in self._heat_data:
                if self._heat_data[pilot_id] and (pilot_id not in self._finished_pilots):
                    Thread(target=start, args=(pilot_id,), daemon=True).start()

    def onRaceStop(self, _args):

        def land(pilot_id):
            self._queue_lock.acquire()
            self.set_sendUID(self._heat_data[pilot_id]['UID'])
            start_col = self.centerOSD(len(self._racestop_message), self._heat_data[pilot_id]['hardware_type'])
            self.send_msg(self._status_row, start_col, self._racestop_message) 
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

        with self._queue_lock:
            for pilot_id in self._heat_data:
                if self._heat_data[pilot_id] and (pilot_id not in self._finished_pilots):
                    Thread(target=land, args=(pilot_id,), daemon=True).start()

    def onRaceLapRecorded(self, args):

        def update_pos(result):
            pilot_id = result['pilot_id']

            self._queue_lock.acquire()
            if not self._position_mode or len(self._heat_data) == 1:
                message = f"LAP: {result['laps'] + 1}"
            else:
                message = f"POSN: {str(result['position']).upper()} | LAP: {result['laps'] + 1}"
            start_col = self.centerOSD(len(message), self._heat_data[pilot_id]['hardware_type'])

            self.set_sendUID(self._heat_data[pilot_id]['UID'])
            self.send_clear_row(self._currentlap_row, self._heat_data[pilot_id]['hardware_type'])
            
            self.send_msg(self._currentlap_row, start_col, message) 
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

        def lap_results(result, gap_info):
            pilot_id = result['pilot_id']

            self._queue_lock.acquire()
            if not self._gap_mode or len(self._heat_data) == 1:
                formatted_time = RHUtils.time_format(gap_info.current.last_lap_time, '{m}:{s}.{d}')
                message = f"x LAP {gap_info.current.lap_number} | {formatted_time} w"
            elif gap_info.next_rank.position:
                formatted_time = RHUtils.time_format(gap_info.next_rank.diff_time, '{m}:{s}.{d}')
                formatted_callsign = str.upper(gap_info.next_rank.callsign)
                message = f"x {formatted_callsign} | +{formatted_time} w"
            else:
                message = self._leader_message
            start_col = self.centerOSD(len(message), self._heat_data[pilot_id]['hardware_type'])
        
            self.set_sendUID(self._heat_data[pilot_id]['UID'])
            self.send_msg(self._lapresults_row, start_col, message)
            self.send_display()
            self.clear_sendUID()
            delay = copy.copy(self._results_uptime)
            self._queue_lock.release()

            time.sleep(delay)

            self._queue_lock.acquire()
            self.set_sendUID(self._heat_data[pilot_id]['UID'])
            self.send_clear_row(self._lapresults_row, self._heat_data[pilot_id]['hardware_type'])
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()


        self._queue_lock.acquire()
        if self._heat_data == {}:
            return

        if args['pilot_done_flag']:
            self._finished_pilots.append(args['pilot_id'])

        results = args['results']['by_race_time']
        for result in results:
            if self._heat_data[result['pilot_id']]:
                
                if result['pilot_id'] not in self._finished_pilots:
                    Thread(target=update_pos, args=(result,), daemon=True).start()

                if (result['pilot_id'] == args['pilot_id']) and (result['laps'] > 0):
                    Thread(target=lap_results, args=(result, args['gap_info']), daemon=True).start()
        
        self._queue_lock.release()
    
    def onLapDelete(self, _args):
        
        def delete(pilot_id):
            self._queue_lock.acquire()
            if self._heat_data[pilot_id]:
                self.set_sendUID(self._heat_data[pilot_id]['UID'])
                self.send_clear_row(10, self._heat_data[pilot_id]['hardware_type'])
                self.send_clear_row(11, self._heat_data[pilot_id]['hardware_type'])
                self.send_clear_row(12, self._heat_data[pilot_id]['hardware_type'])
                self.send_clear_row(13, self._heat_data[pilot_id]['hardware_type'])
                self.send_clear_row(14, self._heat_data[pilot_id]['hardware_type'])
                self.send_display()
                self.clear_sendUID()
            self._queue_lock.release()
        
        with self._queue_lock:
            if self._results_mode:
                for pilot_id in self._heat_data:
                    Thread(target=delete, args=(pilot_id,), daemon=True).start()
            

    def onRacePilotDone(self, args):

        def done(result):

            self._queue_lock.acquire()
            pilot_id = result['pilot_id']
            start_col = self.centerOSD(len(self._pilotdone_message), self._heat_data[pilot_id]['hardware_type'])
        
            self.set_sendUID(self._heat_data[result['pilot_id']]['UID'])
            self.send_clear_row(self._currentlap_row, self._heat_data[pilot_id]['hardware_type'])
            self.send_clear_row(self._status_row, self._heat_data[pilot_id]['hardware_type'])
            self.send_msg(self._status_row, start_col, self._pilotdone_message)

            if self._results_mode:
                self.send_msg(10, 11, "PLACEMENT:")
                self.send_msg(10, 30, str(result['position']))
                self.send_msg(11, 11, "LAPS COMPLETED:")
                self.send_msg(11, 30, str(result['laps']))
                self.send_msg(12, 11, "FASTEST LAP:")
                self.send_msg(12, 30, result['fastest_lap'])
                self.send_msg(13, 11, "FASTEST " + str(result['consecutives_base']) +  " CONSEC:")
                self.send_msg(13, 30, result['consecutives'])
                self.send_msg(14, 11, "TOTAL TIME:")
                self.send_msg(14, 30, result['total_time'])
            
            self.send_display()
            self.clear_sendUID()
            delay = copy.copy(self._finish_uptime)
            self._queue_lock.release()

            time.sleep(delay)

            self._queue_lock.acquire()
            self.set_sendUID(self._heat_data[pilot_id]['UID'])
            self.send_clear_row(self._status_row, self._heat_data[pilot_id]['hardware_type'])
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

        results = args['results']['by_race_time']
        with self._queue_lock:
            for result in results:
                if (self._heat_data[args['pilot_id']]) and (result['pilot_id'] == args['pilot_id']):
                    Thread(target=done, args=(result,), daemon=True).start()
                    break

    def onLapsClear(self, _args):
        
        def clear(pilot_id):
            self._queue_lock.acquire()
            self.set_sendUID(self._heat_data[pilot_id]['UID'])
            self.send_clear()
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

        with self._queue_lock:
            self._finished_pilots = []
            for pilot_id in self._heat_data:
                if self._heat_data[pilot_id]:
                    Thread(target=clear, args=(pilot_id,), daemon=True).start()

    def onSendMessage(self, args):
        
        def notify(pilot):
            self._queue_lock.acquire()
            self.set_sendUID(self._heat_data[pilot]['UID'])
            start_col = self.centerOSD(len(args['message']), self._heat_data[pilot]['hardware_type'])
            self.send_msg(self._announcement_row, start_col, str.upper(args['message']))
            self.send_display()
            self.clear_sendUID()
            delay = copy.copy(self._announcement_uptime)
            self._queue_lock.release()

            time.sleep(delay)

            self._queue_lock.acquire()
            self.set_sendUID(self._heat_data[pilot]['UID'])
            self.send_clear_row(self._announcement_row, self._heat_data[pilot]['hardware_type'])
            self.send_display()
            self.clear_sendUID()
            self._queue_lock.release()

        with self._queue_lock:
            for pilot_id in self._heat_data:
                if self._heat_data[pilot_id]:
                    Thread(target=notify, args=(pilot_id,), daemon=True).start()