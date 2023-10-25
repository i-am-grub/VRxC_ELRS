import logging
import hashlib
import serial
import time
from dataclasses import dataclass
from enum import Enum
from threading import Thread, Lock
from multiprocessing import Process, Queue, Pipe
import serial.tools.list_ports

import RHUtils
from eventmanager import Evt
from RHUI import UIField, UIFieldType, UIFieldSelectOption
from VRxControl import VRxController

logger = logging.getLogger(__name__)

hardwareSettings = {
    'hdzero' : {
        # Do not change the following params:
        'column_size'   : 18,
        'row_size'      : 50,
        # The following params can be changed:
        # Keep value less than or equal to column_size
        # Rows 10-14 are used for post race results if activated
        'status_row'    : 5,
        'position_row'  : 0,
        'gap_row'       : 15,
        'notify_row'    : 6
    }
}

class hardwareOptions(Enum):
    NONE = 'none'
    HDZERO = 'hdzero'

def initialize(rhapi):
    
    controller = elrsBackpack('elrs', 'ELRS', rhapi)
    rhapi.events.on(Evt.VRX_INITIALIZE, controller.registerHandlers)
    
    type_hardware = []
    for option in hardwareOptions:
        type_hardware.append(UIFieldSelectOption(label=option.name, value=option.value))

    hardware = UIField('hardware_type', 'ELRS VRx Hardware', field_type = UIFieldType.SELECT, options = type_hardware)
    rhapi.fields.register_pilot_attribute(hardware)

    enable_bindphrase = UIField('enable_bindphrase', 'Use Bindphase', field_type = UIFieldType.CHECKBOX, value=False)
    rhapi.fields.register_pilot_attribute(enable_bindphrase)

    elrs_bindphrase = UIField(name = 'bindphrase', label = 'Backpack Bindphrase', field_type = UIFieldType.TEXT)
    rhapi.fields.register_pilot_attribute(elrs_bindphrase)

    rhapi.ui.register_panel('elrs_vrxc', 'ExpressLRS VRxC', 'settings', order=0)

    repeat_count_ui = UIField('repeat_count', 'Send Message Repeat Count', field_type = UIFieldType.BASIC_INT, value = 0)
    rhapi.fields.register_option(repeat_count_ui, 'elrs_vrxc')

    post_flight_results = UIField('results_mode', 'Show Post Race Results', field_type = UIFieldType.CHECKBOX)
    rhapi.fields.register_option(post_flight_results, 'elrs_vrxc')

    practice_mode_ui = UIField('practice_mode', 'Practice Mode', field_type = UIFieldType.CHECKBOX)
    rhapi.fields.register_option(practice_mode_ui, 'elrs_vrxc')


def backpack_connector(pipe, queue, rhapi, backpack_port = None):
    version_message = [36, 88, 60, 0, 16, 0, 0, 0, 174]
    target_response = [36, 88, 62, 0, 16, 0, 11, 0]
    mode = 0x00
    logger.info("Attempting to start backpack")
    
    ports = list(serial.tools.list_ports.comports())
    s = serial.Serial(baudrate=460800,
                    bytesize=8, parity='N', stopbits=1,
                    timeout=0.1, xonxoff=0, rtscts=0)
        
    for port in ports:
        s.port = port.device
        s.open()
        s.write(version_message)
        response = s.read(len(target_response))
        if list(response) == target_response:
            logger.info(f"Connected to Backpack on {port.device}")
            break
        else:
            s.close()
    else:
        logger.info("Could not find connected backpack. Ending connector process.")
        mode = -0x01

    while mode != -0x01:
        if pipe.poll():
            mode = pipe.recv()

        # 0x01: Race mode
        if mode == 0x01 or not queue.empty():
            if not queue.empty():
                message = queue.get()
                s.write(message)

        # 0x02: Listen mode
        elif mode == 0x02:
            start_message = [36, 88, 62, 0, 5, 3, 1, 0, 1, 136]
            message = s.read(10)
            if list(message) == start_message:
                logger.info('Attempting to start race from backpack')
                #rhapi.race.stage()
            else:
                time.sleep(0.1)

        # 0x00: Standby mode: Does nothing
        else:
            time.sleep(0.5)

    s.close()

class elrsBackpack(VRxController):
    backpack_process = None
    backpack_conn, rh_conn = Pipe(duplex=False)
    backpack_queue = Queue(maxsize=1000)
    queue_lock = Lock()
    heat_data = {}
    finished_pilots = []
    repeat_count = 0
    results_mode = False
    practice_mode = False

    def __init__(self, name, label, rhapi):
        super().__init__(name, label)
        self._rhapi = rhapi

        self.backpack_process = Process(target=backpack_connector, args=(self.backpack_conn, self.backpack_queue, self._rhapi))
        self.backpack_process.start()

        self._rhapi.events.on(Evt.PILOT_ALTER, self.onPilotAlter)
        self._rhapi.events.on(Evt.OPTION_SET, self.setOptions)        

    def registerHandlers(self, args):
        args['register_fn'](self)

    def setOptions(self, _args = None):
        self.repeat_count = self._rhapi.db.option('repeat_count')
        if self._rhapi.db.option('results_mode') == "1":
            self.results_mode = True
        else:
            self.results_mode = False
        if self._rhapi.db.option('practice_mode') == "1":
            self.practice_mode = True
        else:
            self.practice_mode = False

    #
    # Backpack message generation
    #

    def hash_phrase(self, bindphrase:str) -> list:
        bindingPhraseHash = [x for x in hashlib.md5(("-DMY_BINDING_PHRASE=\"" + bindphrase + "\"").encode()).digest()[0:6]]
        if (bindingPhraseHash[0] % 2) == 1:
            bindingPhraseHash[0] -= 0x01
        return bindingPhraseHash

    def crc8_dvb_s2(self, crc, a):
        crc = crc ^ a
        for ii in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0xD5
            else:
                crc = crc << 1
        return crc & 0xFF
    
    def send_msp(self, body):
        crc = 0
        for x in body:
            crc = self.crc8_dvb_s2(crc, x)
        msp = [ord('$'),ord('X'),ord('<')]
        msp = msp + body
        msp.append(crc)

        if msp[4] != 0x00B4 and msp[4] != 0x00B5:
            for count in range(1 + self.repeat_count):
                self.backpack_queue.put(msp)
        else:
            self.backpack_queue.put(msp)

    def set_UID(self, bindingHash:list, reset = False):
        if reset:
            msp = [0,0x00B4,0x00,7,0,1]
        else:
            msp = [0,0x00B4,0x00,7,0,0]
        for byte in bindingHash:
            msp.append(byte)
        self.send_msp(msp)

    def set_peer(self, bindingHash:list, add:bool = True):
        msp = [0,0x00B5,0x00,7,0]
        for byte in bindingHash:
            msp.append(byte)
        if add:
            msp.append(0x0000)
        else:
            msp.append(0x0001)
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
            #col = int(hardwaretype.row_size / 2) - offset - 1
            col = int(hardwareSettings[hardwaretype]['row_size'] / 2) - offset - 1
        else:
            col = 0
        return col
    
    def send_clear_row(self, row, hardwaretype):
        if hardwaretype:
            l = 4 + hardwareSettings[hardwaretype]['row_size']
        else:
            l = 0
        msp = [0,0x00B6,0x00,l%256,int(l/256),0x03,row,0,0]
        for x in range(hardwareSettings[hardwaretype]['row_size']):
            msp.append(0)
        self.send_msp(msp)

    #
    # VRxC Event Triggers
    #

    def onStartup(self, _args):
        self.rh_conn.send(0x02)

    def onPilotAlter(self, args):
        pilot_id = args['pilot_id']
        if pilot_id in self.heat_data:
            
            if self.heat_data[pilot_id]:
                self.set_peer(self.heat_data[pilot_id]['UID'], False)

            pilot_settings = {}
            pilot_settings['hardware_type'] = self._rhapi.db.pilot_attribute_value(pilot_id, 'hardware_type')
            logger.info(f"Pilot {pilot_id}'s hardware set to {self._rhapi.db.pilot_attribute_value(pilot_id, 'hardware_type')}")

            if self._rhapi.db.pilot_attribute_value(pilot_id, 'enable_bindphrase') == "1":
                UID = self.hash_phrase(self._rhapi.db.pilot_attribute_value(pilot_id, 'bindphrase'))
            else:
                UID = self.hash_phrase(self._rhapi.db.pilot_by_id(pilot_id).callsign)
            pilot_settings['UID'] = UID

            self.heat_data[pilot_id] = pilot_settings
            logger.info(f"Pilot {pilot_id}'s UID set to {UID}")

            self.set_peer(self.heat_data[pilot_id]['UID'], True)

    def onHeatSet(self, args):
        # Clear previous heat peers
        if self.heat_data:
            for pilot_id in self.heat_data:
                if self.heat_data[pilot_id]:
                    self.set_peer(self.heat_data[pilot_id]['UID'], False)

        # Setup new heat
        heat_data = {}
        for slot in self._rhapi.db.slots_by_heat(args['heat_id']):
            if slot.pilot_id:
                hardware_type = self._rhapi.db.pilot_attribute_value(slot.pilot_id, 'hardware_type')
                if hardware_type not in hardwareSettings:
                    heat_data[slot.pilot_id] = None
                    continue

                pilot_settings = {}
                pilot_settings['hardware_type'] = hardware_type
                logger.info(f"Pilot {slot.pilot_id}'s hardware set to {self._rhapi.db.pilot_attribute_value(slot.pilot_id, 'hardware_type')}")

                if self._rhapi.db.pilot_attribute_value(slot.pilot_id, 'enable_bindphrase') == "1":
                    UID = self.hash_phrase(self._rhapi.db.pilot_attribute_value(slot.pilot_id, 'bindphrase'))
                else:
                    UID = self.hash_phrase(self._rhapi.db.pilot_by_id(slot.pilot_id).callsign)
                pilot_settings['UID'] = UID
                
                heat_data[slot.pilot_id] = pilot_settings
                logger.info(f"Pilot {slot.pilot_id}'s UID set to {UID}")
        
        self.heat_data = heat_data

        # Set new heat peers
        for pilot_id in self.heat_data:
            if self.heat_data[pilot_id]:
                self.set_peer(self.heat_data[pilot_id]['UID'], True)

    def onRaceStage(self, args):
        # Set backpack connector to race mode
        self.rh_conn.send(0x01)

        # Set OSD options
        self.setOptions()

        # Setup heat if not done already
        if not self.heat_data:
            self.onHeatSet(args)

        # Send stage message to all pilots
        message = f"w ARM NOW x"
        for pilot_id in self.heat_data:
            if self.heat_data[pilot_id]:
                self.set_UID(self.heat_data[pilot_id]['UID'])
                self.send_clear()
                start_col = self.centerOSD(len(message), self.heat_data[pilot_id]['hardware_type'])
                #self.send_msg(self.heat_data[pilot_id]['hardware_type'].status_row, start_col, message)
                self.send_msg(hardwareSettings[self.heat_data[pilot_id]['hardware_type']]['status_row'], start_col, message)    
                self.send_display()
                self.set_UID(self.heat_data[pilot_id]['UID'], True)  

    def onRaceStart(self, _args):
        message = f"w   GO!   x"
        def start(pilot_id):
            with self.queue_lock:
                self.set_UID(self.heat_data[pilot_id]['UID'])
                self.send_clear()
                start_col = self.centerOSD(len(message), self.heat_data[pilot_id]['hardware_type'])
                #self.send_msg(self.heat_data[pilot_id]['hardware_type'].status_row, start_col, message)
                self.send_msg(hardwareSettings[self.heat_data[pilot_id]['hardware_type']]['status_row'], start_col, message)  
                self.send_display()
                self.set_UID(self.heat_data[pilot_id]['UID'], True)

            time.sleep(.5)

            with self.queue_lock:
                self.set_UID(self.heat_data[pilot_id]['UID'])
                #self.send_clear_row(self.heat_data[pilot]['hardware_type'].status_row, self.heat_data[pilot_id]['hardware_type'])
                self.send_clear_row(hardwareSettings[self.heat_data[pilot_id]['hardware_type']]['status_row'], self.heat_data[pilot_id]['hardware_type'])
                self.send_display()
                self.set_UID(self.heat_data[pilot_id]['UID'], True)

        # Send start message to all pilots
        for pilot_id in self.heat_data:
            if self.heat_data[pilot_id]:
                thread = Thread(target=start, args=(pilot_id,))
                thread.start()

    def onRaceFinish(self, _args):
        message = f"w FINISH LAP! x"
        def start(pilot_id):
            with self.queue_lock:
                self.set_UID(self.heat_data[pilot_id]['UID'])
                self.send_clear_row(hardwareSettings[self.heat_data[pilot_id]['hardware_type']]['status_row'], self.heat_data[pilot_id]['hardware_type'])
                start_col = self.centerOSD(len(message), self.heat_data[pilot_id]['hardware_type'])
                #self.send_msg(self.heat_data[pilot_id]['hardware_type'].status_row, start_col, message)
                self.send_msg(hardwareSettings[self.heat_data[pilot_id]['hardware_type']]['status_row'], start_col, message)  
                self.send_display()
                self.set_UID(self.heat_data[pilot_id]['UID'], True)

            time.sleep(3)

            with self.queue_lock:
                self.set_UID(self.heat_data[pilot_id]['UID'])
                #self.send_clear_row(self.heat_data[pilot]['hardware_type'].status_row, self.heat_data[pilot_id]['hardware_type'])
                self.send_clear_row(hardwareSettings[self.heat_data[pilot_id]['hardware_type']]['status_row'], self.heat_data[pilot_id]['hardware_type'])
                self.send_display()
                self.set_UID(self.heat_data[pilot_id]['UID'], True)

        for pilot_id in self.heat_data:
            if self.heat_data[pilot_id] and (pilot_id not in self.finished_pilots):
                thread = Thread(target=start, args=(pilot_id,))
                thread.start()

    def onRaceStop(self, _args):
        message = f"w  LAND NOW!  x"
        for pilot_id in self.heat_data:
            if self.heat_data[pilot_id]:
                with self.queue_lock:
                    self.set_UID(self.heat_data[pilot_id]['UID'])
                    start_col = self.centerOSD(len(message), self.heat_data[pilot_id]['hardware_type'])
                    #self.send_msg(self.heat_data[pilot_id]['hardware_type'].status_row, start_col, message)
                    self.send_msg(hardwareSettings[self.heat_data[pilot_id]['hardware_type']]['status_row'], start_col, message) 
                    self.send_display()
                    self.set_UID(self.heat_data[pilot_id]['UID'], True)

    def onRaceLapRecorded(self, args):

        def update_pos(result):
            pilot_id = result['pilot_id']

            if self.practice_mode or len(self.heat_data) == 1:
                message = f"LAP: {result['laps'] + 1}"
            else:
                message = f"POSN: {result['position']} | LAP: {result['laps'] + 1}"
            start_col = self.centerOSD(len(message), self.heat_data[pilot_id]['hardware_type'])

            with self.queue_lock:
                self.set_UID(self.heat_data[pilot_id]['UID'])
                #self.send_clear_row(self.heat_data[pilot_id]['hardware_type'].position_row, self.heat_data[pilot_id]['hardware_type'])
                #self.send_msg(self.heat_data[pilot_id]['hardware_type'].position_row, start_col, message)
                self.send_clear_row(hardwareSettings[self.heat_data[pilot_id]['hardware_type']]['position_row'], self.heat_data[pilot_id]['hardware_type'])
                self.send_msg(hardwareSettings[self.heat_data[pilot_id]['hardware_type']]['position_row'], start_col, message) 
                self.send_display()
                self.set_UID(self.heat_data[pilot_id]['UID'], True)

        def lap_results(result, gap_info):
            pilot_id = result['pilot_id']
            if self.practice_mode or len(self.heat_data) == 1:
                formatted_time = RHUtils.time_format(gap_info.current.last_lap_time, '{m}:{s}.{d}')
                message = f"x LAP {gap_info.current.lap_number} | {formatted_time} w"
            elif gap_info.next_rank.position:
                formatted_time = RHUtils.time_format(gap_info.next_rank.diff_time, '{m}:{s}.{d}')
                formatted_callsign = str.upper(gap_info.next_rank.callsign)
                message = f"x {formatted_callsign} | +{formatted_time} w"
            else:
                message = f"x RACE LEADER w"
            start_col = self.centerOSD(len(message), self.heat_data[pilot_id]['hardware_type'])

            with self.queue_lock:
                self.set_UID(self.heat_data[pilot_id]['UID'])
                #self.send_msg(self.heat_data[pilot_id]['hardware_type'].gap_row, start_col, message)
                self.send_msg(hardwareSettings[self.heat_data[pilot_id]['hardware_type']]['gap_row'], start_col, message)
                self.send_display()
                self.set_UID(self.heat_data[pilot_id]['UID'], True)

            time.sleep(5)

            with self.queue_lock:
                self.set_UID(self.heat_data[pilot_id]['UID'])
                #self.send_clear_row(self.heat_data[pilot_id]['hardware_type'].gap_row, self.heat_data[pilot_id]['hardware_type'])
                self.send_clear_row(hardwareSettings[self.heat_data[pilot_id]['hardware_type']]['gap_row'], self.heat_data[pilot_id]['hardware_type'])
                self.send_display()
                self.set_UID(self.heat_data[pilot_id]['UID'], True)

        if args['pilot_done_flag']:
            self.finished_pilots.append(args['pilot_id'])

        results = args['results']['by_race_time']
        for result in results:
            if self.heat_data[result['pilot_id']]:
                
                if result['pilot_id'] not in self.finished_pilots:
                    thread1 = Thread(target=update_pos, args=(result,))
                    thread1.start()

                if (result['pilot_id'] == args['pilot_id']) and (result['laps'] > 0):
                    thread2 = Thread(target=lap_results, args=(result, args['gap_info']))
                    thread2.start()
    
    def onLapDelete(self, _args):
        if self.results_mode:
            for pilot_id in self.heat_data:
                if self.heat_data[pilot_id]:
                    self.set_UID(self.heat_data[pilot_id]['UID'])
                    self.send_clear_row(10, self.heat_data[pilot_id]['hardware_type'])
                    self.send_clear_row(11, self.heat_data[pilot_id]['hardware_type'])
                    self.send_clear_row(12, self.heat_data[pilot_id]['hardware_type'])
                    self.send_clear_row(13, self.heat_data[pilot_id]['hardware_type'])
                    self.send_clear_row(14, self.heat_data[pilot_id]['hardware_type'])
                    self.send_display()
                    self.set_UID(self.heat_data[pilot_id]['UID'], True) 
            

    def onRacePilotDone(self, args):

        def done(result):
            pilot_id = args['pilot_id']
            message = f"w FINISHED! x"
            start_col = self.centerOSD(len(message), self.heat_data[pilot_id]['hardware_type'])
            
            with self.queue_lock:
                self.set_UID(self.heat_data[args['pilot_id']]['UID'])
                self.send_clear_row(hardwareSettings[self.heat_data[pilot_id]['hardware_type']]['position_row'], self.heat_data[pilot_id]['hardware_type'])
                self.send_clear_row(hardwareSettings[self.heat_data[pilot_id]['hardware_type']]['status_row'], self.heat_data[pilot_id]['hardware_type'])
                #self.send_msg(self.heat_data[pilot_id]['hardware_type'].status_row, start_col, message)
                self.send_msg(hardwareSettings[self.heat_data[pilot_id]['hardware_type']]['status_row'], start_col, message)

                if self.results_mode:
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
                self.set_UID(self.heat_data[pilot_id]['UID'], True)

            time.sleep(3)

            with self.queue_lock:
                self.set_UID(self.heat_data[pilot_id]['UID'])
                #self.send_clear_row(self.heat_data[pilot]['hardware_type'].status_row, self.heat_data[pilot_id]['hardware_type'])
                self.send_clear_row(hardwareSettings[self.heat_data[pilot_id]['hardware_type']]['status_row'], self.heat_data[pilot_id]['hardware_type'])
                self.send_display()
                self.set_UID(self.heat_data[pilot_id]['UID'], True)

        results = args['results']['by_race_time']
        for result in results:
            if (self.heat_data[args['pilot_id']]) and (result['pilot_id'] == args['pilot_id']):
                thread = Thread(target=done, args=(result,))
                thread.start()
                break

    def onLapsClear(self, _args):
        self.finished_pilots = []
        for pilot_id in self.heat_data:
            if self.heat_data[pilot_id]:
                with self.queue_lock:
                    self.set_UID(self.heat_data[pilot_id]['UID'])
                    self.send_clear()
                    self.send_display()
                    self.set_UID(self.heat_data[pilot_id]['UID'], True)

        self.rh_conn.send(0x02)

    def onSendMessage(self, args):
        
        def notify(pilot):
            self.set_UID(self.heat_data[pilot]['UID'])
            start_col = self.centerOSD(len(args['message']), self.heat_data[pilot]['hardware_type'])
            #self.send_msg(self.heat_data[pilot]['hardware_type'].notify_row, start_col, str.upper(args['message']))
            self.send_msg(hardwareSettings[self.heat_data[pilot]['hardware_type']]['notify_row'], start_col, str.upper(args['message']))
            self.send_display()
            self.set_UID(self.heat_data[pilot]['UID'], True)

            time.sleep(5)

            self.set_UID(self.heat_data[pilot]['UID'])
            #self.send_clear_row(self.heat_data[pilot]['hardware_type'].notify_row, self.heat_data[pilot]['hardware_type'])
            self.send_clear_row(hardwareSettings[self.heat_data[pilot]['hardware_type']]['notify_row'], self.heat_data[pilot]['hardware_type'])
            self.send_display()
            self.set_UID(self.heat_data[pilot]['UID'], True)

        for pilot_id in self.heat_data:
            if self.heat_data[pilot_id]:
                thread1 = Thread(target=notify, args=(pilot_id,))
                thread1.start()

    def onShutdown(self, _args):
        self.backpack_process.terminate()