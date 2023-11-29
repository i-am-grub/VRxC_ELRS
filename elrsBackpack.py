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

from plugins.VRxC_ELRS.hardware import HARDWARE_SETTINGS
from plugins.VRxC_ELRS.msp import msptypes, msp_message

logger = logging.getLogger(__name__)

class elrsBackpack(VRxController):
    
    _queue_lock = Lock()
    _delay_lock = Lock()
    _connector_status_lock = Lock()
    _repeat_count = 0
    _send_delay = 0.05

    _backpack_connected = True
    
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

        self._racestart_uptime = self._rhapi.db.option('_racestart_uptime') * 1e-1
        self._finish_uptime = self._rhapi.db.option('_finish_uptime') * 1e-1
        self._results_uptime = self._rhapi.db.option('_results_uptime') * 1e-1
        self._announcement_uptime = self._rhapi.db.option('_announcement_uptime') * 1e-1

        self._status_row = self._rhapi.db.option('_status_row')
        self._currentlap_row = self._rhapi.db.option('_currentlap_row')
        self._lapresults_row = self._rhapi.db.option('_lapresults_row')
        self._announcement_row = self._rhapi.db.option('_announcement_row')

        self._repeat_count = self._rhapi.db.option('_bp_repeat')
        with self._delay_lock:
            self._send_delay = self._rhapi.db.option('_bp_delay') * 1e-5

        self._queue_lock.release()

    def start_race(self):
        if self._rhapi.db.option('_race_control'):
            start_race_args = {'start_time_s' : 10}
            if self._rhapi.race.status == RaceStatus.READY:
                self._rhapi.race.stage(start_race_args)

    def stop_race(self):
        if self._rhapi.db.option('_race_control'):
            status = self._rhapi.race.status
            if status == RaceStatus.STAGING or status == RaceStatus.RACING:
                self._rhapi.race.stop()

    #
    # Backpack communications
    #

    def combine_bytes(self, a, b):
        return (b << 8) | a

    def backpack_connector(self):
        version = msp_message()
        version.set_function(msptypes.MSP_ELRS_GET_BACKPACK_VERSION)
        version_message = version.get_msp()
        
        logger.info("Attempting to find backpack")
        
        ports = list(serial.tools.list_ports.comports())
        s = serial.Serial(baudrate=460800,
                        bytesize=8, parity='N', stopbits=1,
                        timeout=0.01, xonxoff=0, rtscts=0,
                        write_timeout=0.01)
        
        #
        # Search for connected backpack
        #

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

            response = list(s.read(8))
            if len(response) == 8:
                if response[:3] == [ord('$'),ord('X'),ord('>')]:
                    mode = self.combine_bytes(response[4], response[5])
                    response_payload_length = self.combine_bytes(response[6], response[7])
                    response_payload = list(s.read(response_payload_length))
                    response_check_sum = list(s.read(1))

                    if mode == msptypes.MSP_ELRS_BACKPACK_SET_MODE:
                        logger.info(f"Connected to backpack on {port.device}")

                        version_list = [chr(val) for val in response_payload]
                        logger.info(f"Backpack version: {''.join(version_list)}")

                        with self._connector_status_lock:
                            self._backpack_connected = True
                        break
            else:
                s.close()
                continue
        else:
            logger.warning("Could not find connected backpack. Ending connector thread.")
            with self._connector_status_lock:
                self._backpack_connected = False

        #
        # Backpack connection loop
        #

        with self._connector_status_lock:
            backpack_connected = copy.copy(self._backpack_connected)
        
        error_count = 0
        while backpack_connected:

            self._delay_lock.acquire()
            delay = copy.copy(self._send_delay)
            self._delay_lock.release()

            # Handle backpack comms 
            while not self._backpack_queue.empty():
                message = self._backpack_queue.get()
                time.sleep(delay)
                
                try:
                    s.write(message)
                except:
                    error_count += 1
                    if error_count > 5:
                        logger.error('Failed to write to backpack. Ending connector thread')
                        s.close()
                        with self._connector_status_lock:
                            self._backpack_connected = False
                        return
                else:
                    error_count = 0

            packet = list(s.read(8))
            if len(packet) == 8:
                if packet[:3] == [ord('$'),ord('X'),ord('<')]:
                    mode = self.combine_bytes(packet[4], packet[5])
                    payload_length = self.combine_bytes(packet[6], packet[7])
                    payload = list(s.read(payload_length))
                    check_sum = list(s.read(1))

                    # Monitor SET_RECORDING_STATE for controling race
                    if mode == msptypes.MSP_ELRS_BACKPACK_SET_RECORDING_STATE:
                        if payload[0] == 0x00:
                            gevent.spawn(self.stop_race)
                        elif payload[0] == 0x01:
                            gevent.spawn(self.start_race)
            
            with self._connector_status_lock:
                backpack_connected = copy.copy(self._backpack_connected)

            time.sleep(0.01)

    #
    # Backpack message generation
    #

    def hash_phrase(self, bindphrase:str) -> list:
        bindingPhraseHash = [x for x in hashlib.md5(("-DMY_BINDING_PHRASE=\"" + bindphrase + "\"").encode()).digest()[0:6]]
        if (bindingPhraseHash[0] % 2) == 1:
            bindingPhraseHash[0] -= 0x01
        return bindingPhraseHash
    
    def centerOSD(self, stringlength, hardwaretype):
        offset = int(stringlength/2)
        if hardwaretype:
            col = int(HARDWARE_SETTINGS[hardwaretype]['row_size'] / 2) - offset
            if col < 0:
                col = 0
        else:
            col = 0
        return col

    def queue_add(self, msp):
        with self._connector_status_lock:
            if self._backpack_connected is False:
                return
        try:
            self._backpack_queue.put(msp, block=False)
        except queue.Full:
            if self._queue_full is False:
                self._queue_full = True
                message = 'ERROR: ELRS Backpack not responding. Please reboot the server to attempt to reconnect.'
                self._rhapi.ui.message_alert(self._rhapi.language.__(message))
        else:
            if self._queue_full is True:
                self._queue_full = False
                message = 'ELRS Backpack has start responding again.'
                self._rhapi.ui.message_notify(self._rhapi.language.__(message))
    
    def send_msp(self, msp):
        self.queue_add(msp)
        if self.combine_bytes(msp[4], msp[5]) == msptypes.MSP_ELRS_SET_OSD:
            for _ in range(self._repeat_count):
                self.queue_add(msp)
            
    def set_sendUID(self, bindingHash:list):
        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_SET_SEND_UID)
        message.set_payload([1] + bindingHash)
        self.send_msp(message.get_msp())

    def clear_sendUID(self):
        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_SET_SEND_UID)
        message.set_payload([0])
        self.send_msp(message.get_msp())

    def send_clear(self):
        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_SET_OSD)
        message.set_payload([0x02])
        self.send_msp(message.get_msp())

    def send_msg(self, row, col, str):
        payload = [0x03,row,col,0]
        for x in [*str]:
            payload.append(ord(x))

        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_SET_OSD)
        message.set_payload(payload)
        self.send_msp(message.get_msp())

    def send_display(self):
        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_SET_OSD)
        message.set_payload([0x04])
        self.send_msp(message.get_msp())
    
    def send_clear_row(self, row, hardwaretype):
        payload = [0x03,row,0,0]
        for x in range(HARDWARE_SETTINGS[hardwaretype]['row_size']):
            payload.append(0)

        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_SET_OSD)
        message.set_payload(payload)
        self.send_msp(message.get_msp())

    def activate_bind(self, _args):
        message = "Activating backpack's bind mode..."
        self._rhapi.ui.message_notify(self._rhapi.language.__(message))
        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_BACKPACK_SET_MODE)
        message.set_payload([ord('B')])
        with self._queue_lock:
            self.send_msp(message.get_msp())
    
    def activate_wifi(self, _args):
        message = "Turning on backpack's wifi..."
        self._rhapi.ui.message_notify(self._rhapi.language.__(message))
        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_BACKPACK_SET_MODE)
        message.set_payload([ord('W')])
        with self._queue_lock:
            self.send_msp(message.get_msp())

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

            hardware_type = self._rhapi.db.pilot_attribute_value(pilot_id, 'hardware_type')
            logger.info(f"Pilot {pilot_id}'s hardware set to {hardware_type}")
            if hardware_type in HARDWARE_SETTINGS:
                pilot_settings['hardware_type'] = hardware_type
            else:
                self._heat_data[pilot_id] = None
                self._queue_lock.release()
                return

            bindphrase = self._rhapi.db.pilot_attribute_value(pilot_id, 'comm_elrs')
            if bindphrase:
                UID = self.hash_phrase(bindphrase)
                pilot_settings['UID'] = UID
            else:
                UID = self.hash_phrase(self._rhapi.db.pilot_by_id(pilot_id).callsign)
                pilot_settings['UID'] = UID

            self._heat_data[pilot_id] = pilot_settings
            logger.info(f"Pilot {pilot_id}'s UID set to {UID}")

        self._queue_lock.release()

    def onHeatSet(self, args):

        heat_data = {}
        for slot in self._rhapi.db.slots_by_heat(args['heat_id']):
            if slot.pilot_id:
                hardware_type = self._rhapi.db.pilot_attribute_value(slot.pilot_id, 'hardware_type')
                if hardware_type not in HARDWARE_SETTINGS:
                    heat_data[slot.pilot_id] = None
                    continue

                pilot_settings = {}
                pilot_settings['hardware_type'] = hardware_type
                logger.info(f"Pilot {slot.pilot_id}'s hardware set to {self._rhapi.db.pilot_attribute_value(slot.pilot_id, 'hardware_type')}")

                bindphrase = self._rhapi.db.pilot_attribute_value(slot.pilot_id, 'comm_elrs')
                if bindphrase:
                    UID = self.hash_phrase(bindphrase)
                    pilot_settings['UID'] = UID
                else:
                    UID = self.hash_phrase(self._rhapi.db.pilot_by_id(slot.pilot_id).callsign)
                    pilot_settings['UID'] = UID
                
                heat_data[slot.pilot_id] = pilot_settings
                logger.info(f"Pilot {slot.pilot_id}'s UID set to {UID}")
        
        self._heat_data = heat_data

    def onRaceStage(self, args):
        # Set OSD options
        self.setOptions()

        # Setup heat if not done already
        with self._queue_lock:
            self.clear_sendUID()
            self._finished_pilots = []
            if not self._heat_data:
                self.onHeatSet(args)

        heat_data = self._rhapi.db.heat_by_id(args['heat_id'])
        class_name = self._rhapi.db.raceclass_by_id(heat_data.class_id).name
        heat_name = heat_data.name
        if heat_data and self._heat_name and class_name and heat_name:
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
            if self._heat_name and class_name and heat_name:
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
                self.send_clear()
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