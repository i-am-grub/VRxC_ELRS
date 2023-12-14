from threading import Lock
import time

class hardwareOption():

    def __init__(self, _queueLock, set_sendUID, clear_sendUID, send_msp, pilot_UID):
        self._queueLock = _queueLock
        self.set_sendUID = set_sendUID
        self.clear_sendUID = clear_sendUID
        self.send_msp = send_msp
        self.pilot_data = pilot_data
        self.pilot_UID = pilot_UID

    def arm(self, pilot):
        return

    def start(self, pilot):
        return

    def finish(self, pilot):
        return
    
    def land(self, result):
        return

    def update_pos(self, result):
        return

    def lap_results(self, result, gap_info):
        return

    def delete(self, pilot):
        return

    def done(self, result):
        return

    def clear(self, pilot):
        return
    
    def notify(self, pilot):
        return

class backpackOSD(hardwareOptions):
    column_size = None
    row_size = None

    def __init__(self, _queueLock, set_sendUID, clear_sendUID, send_msp, pilot_UID):
        super.__init__(self, _queueLock, set_sendUID, clear_sendUID, send_msp, pilot_UID)

    def centerOSD(self, stringlength):
        offset = int(stringlength/2)
        col = int(self.row_size / 2) - offset
        if col < 0:
            col = 0
        return col

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
    
    def send_clear_row(self, row):
        payload = [0x03,row,0,0]
        for x in range(self.row_size):
            payload.append(0)

        message = msp_message()
        message.set_function(msptypes.MSP_ELRS_SET_OSD)
        message.set_payload(payload)
        self.send_msp(message.get_msp())

    def arm(self, pilot):
        self._queueLock.acquire()
        self.set_sendUID(self.pilot_UID)
        self.send_clear()
        start_col1 = self.centerOSD(len(self._racestage_message))
        self.send_msg(self._status_row, start_col1, self._racestage_message)
        if self._heat_name and heat_name:
            start_col2 = self.centerOSD(len(race_name))
            self.send_msg(self._announcement_row, start_col2, race_name)
        self.send_display()
        self.clear_sendUID()
        self._queueLock.release()

    def start(self, pilot):
        
        self._queueLock.acquire()
        self.set_sendUID(self._heat_data[pilot_id]['UID'])
        self.send_clear()
        start_col = self.centerOSD(len(self._racestart_message), self._heat_data[pilot_id]['hardware_type'])
        self.send_msg(self._status_row, start_col, self._racestart_message)  
        self.send_display()
        self.clear_sendUID()
        delay = copy.copy(self._racestart_uptime)
        self._queueLock.release()

        time.sleep(delay)

        self._queueLock.acquire()
        self.set_sendUID(self._heat_data[pilot_id]['UID'])
        self.send_clear_row(self._status_row, self._heat_data[pilot_id]['hardware_type'])
        self.send_display()
        self.clear_sendUID()
        self._queueLock.release()

    def finish(self, pilot):
        
        self._queueLock.acquire()
        self.set_sendUID(self._heat_data[pilot_id]['UID'])
        self.send_clear_row(self._status_row, self._heat_data[pilot_id]['hardware_type'])
        start_col = self.centerOSD(len(self._racefinish_message), self._heat_data[pilot_id]['hardware_type'])
        self.send_msg(self._status_row, start_col, self._racefinish_message)  
        self.send_display()
        self.clear_sendUID()
        delay = copy.copy(self._finish_uptime)
        self._queueLock.release()

        time.sleep(delay)

        self._queueLock.acquire()
        self.set_sendUID(self._heat_data[pilot_id]['UID'])
        self.send_clear_row(self._status_row, self._heat_data[pilot_id]['hardware_type'])
        self.send_display()
        self.clear_sendUID()
        self._queueLock.release()
    
    def land(self, result):
        
        self._queueLock.acquire()
        self.set_sendUID(self._heat_data[pilot_id]['UID'])
        start_col = self.centerOSD(len(self._racestop_message), self._heat_data[pilot_id]['hardware_type'])
        self.send_msg(self._status_row, start_col, self._racestop_message) 
        self.send_display()
        self.clear_sendUID()
        self._queueLock.release()

    def update_pos(self, result):
        pilot_id = result['pilot_id']

        self._queueLock.acquire()
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
        self._queueLock.release()

    def lap_results(self, result, gap_info):
        pilot_id = result['pilot_id']

        self._queueLock.acquire()
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
        self._queueLock.release()

        time.sleep(delay)

        self._queueLock.acquire()
        self.set_sendUID(self._heat_data[pilot_id]['UID'])
        self.send_clear_row(self._lapresults_row, self._heat_data[pilot_id]['hardware_type'])
        self.send_display()
        self.clear_sendUID()
        self._queueLock.release()

    def delete(self, pilot):
        self._queueLock.acquire()
        if self._heat_data[pilot_id]:
            self.set_sendUID(self._heat_data[pilot_id]['UID'])
            self.send_clear()
            self.send_display()
            self.clear_sendUID()
        self._queueLock.release()

    def done(self, result):
        self._queueLock.acquire()
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
        self._queueLock.release()

        time.sleep(delay)

        self._queueLock.acquire()
        self.set_sendUID(self._heat_data[pilot_id]['UID'])
        self.send_clear_row(self._status_row, self._heat_data[pilot_id]['hardware_type'])
        self.send_display()
        self.clear_sendUID()
        self._queueLock.release()

    def clear(self, pilot):
        self._queueLock.acquire()
        self.set_sendUID(self._heat_data[pilot_id]['UID'])
        self.send_clear()
        self.send_display()
        self.clear_sendUID()
        self._queueLock.release()
    
    def notify(self, pilot):
        self._queueLock.acquire()
        self.set_sendUID(self.pilot_UID)
        start_col = self.centerOSD(len(args['message']))
        self.send_msg(self._announcement_row, start_col, str.upper(args['message']))
        self.send_display()
        self.clear_sendUID()
        delay = copy.copy(self._announcement_uptime)
        self._queueLock.release()

        time.sleep(delay)

        self._queueLock.acquire()
        self.set_sendUID(self.pilot_UID)
        self.send_clear_row(self._announcement_row)
        self.send_display()
        self.clear_sendUID()
        self._queueLock.release()

class mspOSD(hardwareOptions):
    
    def __init__(self, _queueLock, set_sendUID, clear_sendUID, send_msp, pilot_UID):
        super.__init__(self, _queueLock, set_sendUID, clear_sendUID, send_msp, pilot_UID)

class hdzero(backpackOSD):
    column_size = 18
    row_size = 50

    def __init__(self, _queueLock, set_sendUID, clear_sendUID, send_msp, pilot_UID):
        super.__init__(self, _queueLock, set_sendUID, clear_sendUID, send_msp, pilot_UID)
    