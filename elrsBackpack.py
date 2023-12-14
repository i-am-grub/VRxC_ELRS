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
from RHGPIO import RealRPiGPIOFlag
if RealRPiGPIOFlag:
    import RPi.GPIO as GPIO

import plugins.VRxC_ELRS.hardwareConfig as hardwareConfig
from plugins.VRxC_ELRS.msp import msptypes, msp_message

logger = logging.getLogger(__name__)

class elrsBackpack(VRxController):
    
    _backpackQueue = queue.Queue(maxsize=200)
    _queueLock = Lock()
    _connector_status_lock = Lock()

    _backpack_connected = False

    HARDWARE_CONFIGS = {
        'hdzero'    : hardwareConfig.hdzero,
        'msp_osd'   : hardwareConfig.mspOSD,
    }
    
    _heat_name = None
    _heat_data = {}
    _finished_pilots = []
    _queue_full = False

    def __init__(self, name, label, rhapi):
        super().__init__(name, label)
        self._rhapi = rhapi

        Thread(target=self.backpack_connector, daemon=True).start()

    def registerHandlers(self, args):
        args['register_fn'](self)

    def setOptions(self, _args = None):

        self._queueLock.acquire()

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

        self._queueLock.release()

    def start_race(self):
        if self._rhapi.db.option('_race_control') == '1':
            start_race_args = {'start_time_s' : 10}
            if self._rhapi.race.status == RaceStatus.READY:
                self._rhapi.race.stage(start_race_args)

    def stop_race(self):
        if self._rhapi.db.option('_race_control') == '1':
            status = self._rhapi.race.status
            if status == RaceStatus.STAGING or status == RaceStatus.RACING:
                self._rhapi.race.stop()

    def reboot_esp(self, _args):
        if RealRPiGPIOFlag:
            GPIO.output(11, GPIO.LOW)
            time.sleep(1)
            GPIO.output(11, GPIO.HIGH)
            message = "Cycle Complete"
            self._rhapi.ui.message_notify(self._rhapi.language.__(message))

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
                logger.warning('Failed to open serial device. Attempting to connect to new device...')
                continue
            
            time.sleep(1.5) # Needed for connecting to DevKitC

            try:
                s.write(version_message)
            except:
                logger.error('Failed to write to open serial device. Attempting to connect to new device...')
                s.close()
                continue

            response = list(s.read(8))
            if len(response) == 8:
                logger.info(f'Device response: {response}')
                if response[:3] == [ord('$'),ord('X'),ord('>')]:
                    mode = self.combine_bytes(response[4], response[5])
                    response_payload_length = self.combine_bytes(response[6], response[7])
                    response_payload = list(s.read(response_payload_length))
                    response_check_sum = list(s.read(1))

                    if mode == msptypes.MSP_ELRS_BACKPACK_SET_MODE or mode == msptypes.MSP_ELRS_GET_BACKPACK_VERSION:
                        logger.info(f"Connected to backpack on {port.device}")

                        version_list = [chr(val) for val in response_payload]
                        logger.info(f"Backpack version: {''.join(version_list)}")

                        with self._connector_status_lock:
                            self._backpack_connected = True
                        break
                    
                    else:
                        logger.warning(f"Unexpected response from {port.device}, trying next port...")
                        s.close()
                        continue
                else:
                    logger.warning(f"Unrecongnized response from {port.device}, trying next port...")
                    s.close()
                    continue
            else:
                logger.warning(f"Bad response from {port.device}, trying next port...")
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

            # Handle backpack comms 
            while not self._backpackQueue.empty():
                message = self._backpackQueue.get()
                s.flush()
                
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

                    # Monitor SET_RECORDING_STATE for controlling race
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


    def queue_add(self, msp):
        with self._connector_status_lock:
            if self._backpack_connected is False:
                return
        try:
            self._backpackQueue.put(msp, block=False)
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

    def activate_bind(self, _args):
        message = "Activating backpack's bind mode..."
        self._rhapi.ui.message_notify(self._rhapi.language.__(message))
        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_BACKPACK_SET_MODE)
        message.set_payload([ord('B')])
        with self._queueLock:
            self.send_msp(message.get_msp())
    
    def activate_wifi(self, _args):
        message = "Turning on backpack's wifi..."
        self._rhapi.ui.message_notify(self._rhapi.language.__(message))
        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_BACKPACK_SET_MODE)
        message.set_payload([ord('W')])
        with self._queueLock:
            self.send_msp(message.get_msp())

    #
    # VRxC Event Triggers
    #

    def onPilotAlter(self, args):
        pilot_id = args['pilot_id']
        self._queueLock.acquire()

        if pilot_id in self._heat_data:
            pilot_settings = {}

            hardware_type = self._rhapi.db.pilot_attribute_value(pilot_id, 'hardware_type')
            logger.info(f"Pilot {pilot_id}'s hardware set to {hardware_type}")
            if hardware_type in HARDWARE_SETTINGS:
                pilot_settings['hardware_type'] = hardware_type
            else:
                self._heat_data[pilot_id] = None
                self._queueLock.release()
                return

            bindphrase = self._rhapi.db.pilot_attribute_value(pilot_id, 'comm_elrs')
            if bindphrase:
                UID = self.hash_phrase(bindphrase)
            else:
                UID = self.hash_phrase(self._rhapi.db.pilot_by_id(pilot_id).callsign)

            self._heat_data[pilot_id] = self.HARDWARE_CONFIGS[hardware_type](self._queueLock, self.set_sendUID, 
                                                                                self.clear_sendUID, self.send_msp, UID)
            logger.info(f"Pilot {pilot_id}'s UID set to {UID}")

        self._queueLock.release()

    def onHeatSet(self, args):

        heat_data = {}
        for slot in self._rhapi.db.slots_by_heat(args['heat_id']):
            if slot.pilot_id:
                hardware_type = self._rhapi.db.pilot_attribute_value(slot.pilot_id, 'hardware_type')
                if hardware_type not in HARDWARE_SETTINGS:
                    heat_data[slot.pilot_id] = None
                    continue

                bindphrase = self._rhapi.db.pilot_attribute_value(slot.pilot_id, 'comm_elrs')
                if bindphrase:
                    UID = self.hash_phrase(bindphrase)
                else:
                    UID = self.hash_phrase(self._rhapi.db.pilot_by_id(slot.pilot_id).callsign)
                
                heat_data[slot.pilot_id] = self.HARDWARE_CONFIGS[hardware_type](self._queueLock, self.set_sendUID, 
                                                                                self.clear_sendUID, self.send_msp, UID)
                logger.info(f"Pilot {slot.pilot_id}'s UID set to {UID}")
        
        self._heat_data = heat_data

    def onRaceStage(self, args):
        # Set OSD options
        self.setOptions()

        # Setup heat if not done already
        with self._queueLock:
            self.clear_sendUID()
            self._finished_pilots = []
            if not self._heat_data:
                self.onHeatSet(args)

        heat_data = self._rhapi.db.heat_by_id(args['heat_id'])
        if heat_data:
            class_id = heat_data.class_id
            heat_name = heat_data.name
        else:
            class_id = None
            heat_name = None
        if class_id:
            raceclass = self._rhapi.db.raceclass_by_id(class_id)
        else:
            raceclass = None
        if raceclass:
            class_name = raceclass.name
        else:
            class_name = None
        if self._heat_name and heat_data and class_name and heat_name:
            round_trans = self._rhapi.__('Round')
            round_num = self._rhapi.db.heat_max_round(args['heat_id']) + 1
            if round_num > 1:
                race_name = f'x {class_name.upper()} | {heat_name.upper()} | {round_trans.upper()} {round_num} w'
            else:
                race_name = f'x {class_name.upper()} | {heat_name.upper()} w'
        elif self._heat_name and heat_data and heat_name:
            race_name = f'x {heat_name.upper()} w'

        # Send stage message to all pilots
        with self._queueLock:
            for pilot_id in self._heat_data:
                if self._heat_data[pilot_id]:
                    Thread(target=self._heat_data[pilot_id].arm, args=(pilot_id,), daemon=True).start()

    def onRaceStart(self, _args):

        with self._queueLock:
            for pilot_id in self._heat_data:
                if self._heat_data[pilot_id]:
                    Thread(target=self._heat_data[pilot_id].start, args=(pilot_id,), daemon=True).start()

    def onRaceFinish(self, _args):

        with self._queueLock:
            for pilot_id in self._heat_data:
                if self._heat_data[pilot_id] and (pilot_id not in self._finished_pilots):
                    Thread(target=self._heat_data[pilot_id].finish, args=(pilot_id,), daemon=True).start()

    def onRaceStop(self, _args):

        with self._queueLock:
            for pilot_id in self._heat_data:
                if self._heat_data[pilot_id] and (pilot_id not in self._finished_pilots):
                    Thread(target=self._heat_data[pilot_id].land, args=(pilot_id,), daemon=True).start()

    def onRaceLapRecorded(self, args):

        self._queueLock.acquire()
        if self._heat_data == {}:
            self._queueLock.release()
            return

        if args['pilot_done_flag']:
            self._finished_pilots.append(args['pilot_id'])

        results = args['results']['by_race_time']
        for result in results:
            if self._heat_data[result['pilot_id']]:
                
                if result['pilot_id'] not in self._finished_pilots:
                    Thread(target=self._heat_data[pilot_id].update_pos, args=(result,), daemon=True).start()

                if (result['pilot_id'] == args['pilot_id']) and (result['laps'] > 0):
                    Thread(target=self._heat_data[pilot_id].lap_results, args=(result, args['gap_info']), daemon=True).start()
        
        self._queueLock.release()
    
    def onLapDelete(self, _args):
        
        with self._queueLock:
            if self._results_mode:
                for pilot_id in self._heat_data:
                    Thread(target=self._heat_data[pilot_id].delete, args=(pilot_id,), daemon=True).start()
            

    def onRacePilotDone(self, args):

        results = args['results']['by_race_time']
        with self._queueLock:
            for result in results:
                if (self._heat_data[args['pilot_id']]) and (result['pilot_id'] == args['pilot_id']):
                    Thread(target=self._heat_data[pilot_id].done, args=(result,), daemon=True).start()
                    break

    def onLapsClear(self, _args):          

        with self._queueLock:
            self._finished_pilots = []
            for pilot_id in self._heat_data:
                if self._heat_data[pilot_id]:
                    Thread(target=self._heat_data[pilot_id].clear, args=(pilot_id,), daemon=True).start()

    def onSendMessage(self, args):

        with self._queueLock:
            for pilot in self._heat_data:
                if self._heat_data[pilot_id]:
                    Thread(target=self._heat_data[pilot_id].notify, args=(pilot,), daemon=True).start()