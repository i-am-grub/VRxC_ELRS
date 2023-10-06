import logging
import hashlib
import serial
import time
from dataclasses import dataclass
from enum import Enum
from threading import Thread, Lock
from multiprocessing import Process, Queue, Pipe
import esptool

from eventmanager import Evt
from RHUI import UIField, UIFieldType, UIFieldSelectOption
from VRxControl import VRxController

logger = logging.getLogger(__name__)

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

def backpack_connector(pipe, queue, rhapi):
        ports = esptool.get_port_list()
        s = serial.Serial(port=ports[-1], baudrate=460800, # For inital rapid development only: connecting to the last port in the port list
                        bytesize=8, parity='N', stopbits=1,
                        timeout=0.1, xonxoff=0, rtscts=0)
        mode = 0x00
        while mode != -0x01:
            # Check if a different mode has
            if pipe.poll():
                mode = pipe.recv()

            # 0x01: Race mode - only pay attend to the messages waiting to be sent in the queue
            if mode == 0x01 or not queue.empty():
                if not queue.empty():
                    message = queue.get()
                    s.write(message)
                    s.read(10)

            # 0x02: Listen mode - listens for messages from the backpack. If the backpack sends the correct message, start the race
            elif mode == 0x02: 
                message = s.read(10)
            # TODO: Set the response needed from the backpack to stage the race
            #    if message: 
            #         rhapi.race.stage()
            #         mode = 0x00

            # 0x00: Standby mode: Does nothing
            else:
                time.sleep(0.5)

@dataclass
class hardwareType():
    column_size: int
    row_size: int
    status_row: int
    position_row: int
    gap_row: int
    results_row: int
    notify_row: int

# Switch over to objects when database supports it. Use a lookup dict for the time being
class hardwareOptions(Enum):
    NONE = 'none' # hardwareType(0, 0, 0, 0, 0, 0, 0)
    HDZERO = 'hdzero' # hardwareType(18, 50, 5, 0, 15, 10, 6)

hardwaredict = {
    'none' : {
        'column_size'   : 0,
        'row_size'      : 0,
        'status_row'    : 0,
        'position_row'  : 0,
        'gap_row'       : 0,
        'results_row'   : 0,
        'notify_row'    : 0, 
        },
    'hdzero' : {
        'column_size'   : 18,
        'row_size'      : 50,
        'status_row'    : 5,
        'position_row'  : 0,
        'gap_row'       : 15,
        'results_row'   : 10,
        'notify_row'    : 6
    }
}

class elrsBackpack(VRxController):
    backpack_process = None
    backpack_conn, rh_conn = Pipe(duplex=False)
    backpack_queue = Queue()
    queue_lock = Lock()
    backpack_OSDrange = { '2' : {'column_size' : 18, 'row_size' : 50}}
    heat_data = {}

    def __init__(self, name, label, rhapi):
        super().__init__(name, label)
        self._rhapi = rhapi

        # This spawned backpack process is independent from RotorHazard, allowing it to communicate with the backpack as quickly as possible
        self.backpack_process = Process(target=backpack_connector, args=(self.backpack_conn, self.backpack_queue, self._rhapi))
        self.backpack_process.start()

        self._rhapi.events.on(Evt.RACE_PILOT_DONE, self.onRacePilotDone)

    def registerHandlers(self, args):
        args['register_fn'](self)

    # Generate the UID from the input string
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
        self.backpack_queue.put(msp) # Add the generated message to the queue for the backpack process to send to the backpack

    # Set the address the messages will be sent to (the backpack will also change it's wifi mac address to join the pilot's backpack network)
    def set_UID(self, bindingHash:list, reset = False):
        if reset:
            msp = [0,0x00B4,0x00,7,0,1]
        else:
            msp = [0,0x00B4,0x00,7,0,0]
        for byte in bindingHash:
            msp.append(byte)
        self.send_msp(msp)

    # Register (or unregister due to the 20 peer limit of espnow) the pilot's send address from espnow
    def set_peer(self, bindingHash:list, add:bool = True):
        msp = [0,0x00B5,0x00,7,0]
        for byte in bindingHash:
            msp.append(byte)
        if add:
            msp.append(0x0000)
        else:
            msp.append(0x0001)
        self.send_msp(msp)

    # Reset the backpack OSD design (not cleared until send_display is sent)
    def send_clear(self):
        msp = [0,0x00B6,0x00,1,0,0x02]
        self.send_msp(msp)

    # Add to OSD design (not shown until send_display is sent)
    def send_msg(self, row, col, str):
        l = 4+len(str)
        msp = [0,0x00B6,0x00,l%256,int(l/256),0x03,row,col,0]
        for x in [*str]:
            msp.append(ord(x))
        self.send_msp(msp)

    # Show the designed OSD 
    def send_display(self):
        msp = [0,0x00B6,0x00,1,0,0x04]
        self.send_msp(msp)

    # Center the message for the hardware type
    def centerOSD(self, stringlength, hardwaretype):
        offset = int(stringlength/2)
        if hardwaretype:
            #col = int(hardwaretype.row_size / 2) - offset - 1
            col = int(hardwaredict[hardwaretype]['row_size'] / 2) - offset - 1
        else:
            col = 0
        return col
    
    # Center the string based on the hardware the pilot is viewing it from
    def send_clear_row(self, row, hardwaretype):
        if hardwaretype:
            l = 4 + hardwaredict[hardwaretype]['row_size']
        else:
            l = 0
        msp = [0,0x00B6,0x00,l%256,int(l/256),0x03,row,0,0]
        for x in range(hardwaredict[hardwaretype]['row_size']):
            msp.append(0)
        self.send_msp(msp)

    def onHeatSet(self, args):
        # Unregister all pilots' backpack as espnow peers from the previous heat
        if self.heat_data:
            for pilot in self.heat_data:
                self.set_peer(self.heat_data[pilot]['UID'], False)

        # Generate the dict for newly set heat
        heat_data = {}
        for slot in self._rhapi.db.slots_by_heat(args['heat_id']):
            if slot.pilot_id:
                pilot_settings = {}
                pilot_settings['hardware_type'] = self._rhapi.db.pilot_attribute_value(slot.pilot_id, 'hardware_type')
                logger.info(f"Pilot {slot.pilot_id}'s hardware set to {self._rhapi.db.pilot_attribute_value(slot.pilot_id, 'hardware_type')}")

                if self._rhapi.db.pilot_attribute_value(slot.pilot_id, 'enable_bindphrase') == "1":             # If 'Use Bindphrase' is not checked, use the pilot's callsign
                    UID = self.hash_phrase(self._rhapi.db.pilot_attribute_value(slot.pilot_id, 'bindphrase'))
                else:
                    UID = self.hash_phrase(self._rhapi.db.pilot_by_id(slot.pilot_id).callsign)
                pilot_settings['UID'] = UID
                
                heat_data[slot.pilot_id] = pilot_settings
                logger.info(f"Pilot {slot.pilot_id}'s UID set to {UID}")
        
        self.heat_data = heat_data

        # Register all pilots' backpacks as espnow peers for the set heat
        for pilot in self.heat_data:
            self.set_peer(self.heat_data[pilot]['UID'], True)

    def onRaceStage(self, _args):
        self.rh_conn.send(0x01) # Set the backpack process to race mode

        # Currently using a fixed
        message = "w ARM NOW x"
        
        for pilot in self.heat_data:                                                                                # Example workflow:
            self.set_UID(self.heat_data[pilot]['UID'])                                                              # Set up the backpack's addresses to communiate with a specific pilot
            self.send_clear()                                                                                       # Clear any previous messages on pilot's OSD
            start_col = self.centerOSD(len(message), self.heat_data[pilot]['hardware_type'])               # Center the message for the hardware type
            #self.send_msg(self.heat_data[pilot]['hardware_type'].status_row, start_col, message)                    # Send the message to design the OSD
            self.send_msg(hardwaredict[self.heat_data[pilot]['hardware_type']]['status_row'], start_col, message)    
            self.send_display()                                                                                     # Show the OSD to the pilot
            self.set_UID(self.heat_data[pilot]['UID'], True)                                                        # Reset the backpack's addresses to the firmeware default

    def onRaceStart(self, _args):
        
        message = "w   GO!   x"
        def start(pilot):
            for index in range(3): # Flash the start message 3 times
                with self.queue_lock: # Lock the ability for other threads to write to the backpack process queue. This makes sure that the messages are set in the right order.
                    self.set_UID(self.heat_data[pilot]['UID'])
                    self.send_clear()
                    start_col = self.centerOSD(len(message), self.heat_data[pilot]['hardware_type'])
                    #self.send_msg(self.heat_data[pilot]['hardware_type'].status_row, start_col, message)
                    self.send_msg(hardwaredict[self.heat_data[pilot]['hardware_type']]['status_row'], start_col, message)  
                    self.send_display()
                    self.set_UID(self.heat_data[pilot]['UID'], True)

                time.sleep(.2) # time delay before the message disappears. 

                with self.queue_lock:
                    self.set_UID(self.heat_data[pilot]['UID'])
                    #self.send_clear_row(self.heat_data[pilot]['hardware_type'].status_row, self.heat_data[pilot]['hardware_type']) # only clear the row of the message
                    self.send_clear_row(hardwaredict[self.heat_data[pilot]['hardware_type']]['status_row'], self.heat_data[pilot]['hardware_type'])
                    self.send_display()
                    self.set_UID(self.heat_data[pilot]['UID'], True)

                time.sleep(.2) # time delay before teh message is allowed to reappear

        for pilot in self.heat_data: # Spawn a thread for each pilot so we can use time.sleep without affecting the execution speed of RotorHazard
            thread = Thread(target=start, args=(pilot,))
            thread.start()

    def onRaceStop(self, _args):
        message = "wLAND NOW!x"
        for pilot in self.heat_data:
            with self.queue_lock:
                self.set_UID(self.heat_data[pilot]['UID'])
                start_col = self.centerOSD(len(message), self.heat_data[pilot]['hardware_type'])
                #self.send_msg(self.heat_data[pilot]['hardware_type'].status_row, start_col, message)
                self.send_msg(hardwaredict[self.heat_data[pilot]['hardware_type']]['status_row'], start_col, message) 
                self.send_display()
                self.set_UID(self.heat_data[pilot]['UID'], True)

    def onRaceLapRecorded(self, args):

        def update_pos(result):
            pilot = result['pilot_id']
            message = f"LAP: {result['laps']} | POSN: {result['position']}"
            start_col = self.centerOSD(len(message), self.heat_data[pilot]['hardware_type'])

            with self.queue_lock:
                self.set_UID(self.heat_data[pilot]['UID'])
                #self.send_clear_row(self.heat_data[pilot]['hardware_type'].position_row, self.heat_data[pilot]['hardware_type'])
                #self.send_msg(self.heat_data[pilot]['hardware_type'].position_row, start_col, message)
                self.send_clear_row(hardwaredict[self.heat_data[pilot]['hardware_type']]['position_row'], self.heat_data[pilot]['hardware_type'])
                self.send_msg(hardwaredict[self.heat_data[pilot]['hardware_type']]['position_row'], start_col, message) 
                self.send_display()
                self.set_UID(self.heat_data[pilot]['UID'], True)

        def lap_results(result, gap_info):
            pilot = result['pilot_id']
            if gap_info.next_rank.position:
                message = f"x {gap_info.next_rank.callsign} | +{gap_info.next_rank.diff_time} w" # Appears to be broke at the moment
            else:
                message = f"x RACE LEADER w"
            start_col = self.centerOSD(len(message), self.heat_data[pilot]['hardware_type'])

            with self.queue_lock:
                self.set_UID(self.heat_data[pilot]['UID'])
                #self.send_msg(self.heat_data[pilot]['hardware_type'].gap_row, start_col, message)
                self.send_msg(hardwaredict[self.heat_data[pilot]['hardware_type']]['gap_row'], start_col, message)
                self.send_display()
                self.set_UID(self.heat_data[pilot]['UID'], True)

            time.sleep(5)

            with self.queue_lock:
                self.set_UID(self.heat_data[pilot]['UID'])
                #self.send_clear_row(self.heat_data[pilot]['hardware_type'].gap_row, self.heat_data[pilot]['hardware_type'])
                self.send_clear_row(hardwaredict[self.heat_data[pilot]['hardware_type']]['gap_row'], self.heat_data[pilot]['hardware_type'])
                self.send_display()
                self.set_UID(self.heat_data[pilot]['UID'], True)


        results = args['results']['by_race_time']
        for result in results: # Update the position and laps of all pilots
            thread1 = Thread(target=update_pos, args=(result,))
            thread1.start()

            if (result['pilot_id'] == args['pilot_id']) and (result['laps'] > 0): # Show the laps results only to the pilot that made the lap
                thread2 = Thread(target=lap_results, args=(result, args['gap_info']))
                thread2.start()

    def onRacePilotDone(self, args):

        def done(result):
            pilot = args['pilot_id']
            message = "wFINISHED!x"
            start_col = self.centerOSD(len(message), self.heat_data[pilot]['hardware_type'])
            
            with self.queue_lock:
                self.set_UID(self.heat_data[args['pilot_id']]['UID'])
                #self.send_msg(self.heat_data[pilot]['hardware_type'].status_row, start_col, message)
                self.send_msg(hardwaredict[self.heat_data[pilot]['hardware_type']]['status_row'], start_col, message) 
                self.send_msg(10, 11, "LAPS COMPLETED:")
                self.send_msg(10, 30, str(result['laps']))
                self.send_msg(11, 11, "FASTEST LAP:")
                self.send_msg(11, 30, result['fastest_lap'])
                self.send_msg(12, 11, "FASTEST " + str(result['consecutives_base']) +  " CONSEC:")
                self.send_msg(12, 30, result['consecutives'])
                self.send_msg(13, 11, "TOTAL TIME:")
                self.send_msg(13, 30, result['total_time'])
                self.send_display()
                self.set_UID(self.heat_data[pilot]['UID'], True)

        results = args['results']['by_race_time']
        for result in results:
            if result['pilot_id'] == args['pilot_id']: # Show the race results only to the pilot that made the lap
                thread = Thread(target=done, args=(result,))
                thread.start()
                break

    def onLapsClear(self, _args):
        for pilot in self.heat_data: # Clear all pilot's OSD after the race
            with self.queue_lock:
                self.set_UID(self.heat_data[pilot]['UID'])
                self.send_clear()
                self.send_display()
                self.set_UID(self.heat_data[pilot]['UID'], True)

        self.rh_conn.send(0x00) # Set the backpack process to Standby mode. When Listen mode is complete, switch to  0x02

    def onSendMessage(self, args):
        
        def notify(pilot):
            self.set_UID(self.heat_data[pilot]['UID'])
            start_col = self.centerOSD(len(args['message']), self.heat_data[pilot]['hardware_type'])
            #self.send_msg(self.heat_data[pilot]['hardware_type'].notify_row, start_col, str.upper(args['message']))
            self.send_msg(hardwaredict[self.heat_data[pilot]['hardware_type']]['notify_row'], start_col, str.upper(args['message']))
            self.send_display()
            self.set_UID(self.heat_data[pilot]['UID'], True)

            time.sleep(5)

            self.set_UID(self.heat_data[pilot]['UID'])
            #self.send_clear_row(self.heat_data[pilot]['hardware_type'].notify_row, self.heat_data[pilot]['hardware_type'])
            self.send_clear_row(hardwaredict[self.heat_data[pilot]['hardware_type']]['notify_row'], self.heat_data[pilot]['hardware_type'])
            self.send_display()
            self.set_UID(self.heat_data[pilot]['UID'], True)

        for pilot in self.heat_data:
            thread1 = Thread(target=notify, args=(pilot,))
            thread1.start()

    def onShutdown(self, _args):
        self.backpack_process.terminate()
