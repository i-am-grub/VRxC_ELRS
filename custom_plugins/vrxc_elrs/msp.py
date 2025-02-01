from dataclasses import dataclass

class msp_message():

    _payload = []

    def _convert_values(self, value):
        a = value%256
        b = int(value/256)
        return [a, b]

    def set_function(self, function):
        self._function = self._convert_values(function)

    def set_payload(self, payload:list):
        self._payload = payload

    def _payload_size(self):
        size = len(self._payload)
        return self._convert_values(size)

    def _crc8_dvb_s2(self, crc, a):
        crc = crc ^ a
        for ii in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0xD5
            else:
                crc = crc << 1
        return crc & 0xFF
    
    def _calculate_checksum(self, body):
        crc = 0
        for x in body:
            crc = self._crc8_dvb_s2(crc, x)
        return crc

    def get_msp(self):
        msp = [ord('$'), ord('X'), ord('<')]

        body = [0]
        body += self._function
        body += self._payload_size()
        body += self._payload
        
        checksum = self._calculate_checksum(body)
        msp += body + [checksum]
        return msp

#
# ExpressLRS Backpack MSPTypes
#

@dataclass
class msptypes():

    MSP_ELRS_FUNC                           = 0x4578 # ['E','x']

    MSP_SET_RX_CONFIG                       = 45
    MSP_VTX_CONFIG                          = 88   #out message         Get vtx settings - betaflight
    MSP_SET_VTX_CONFIG                      = 89   #in message          Set vtx settings - betaflight
    MSP_EEPROM_WRITE                        = 250  #in message          no param

    # ELRS specific opcodes
    MSP_ELRS_RF_MODE                        = 0x06
    MSP_ELRS_TX_PWR                         = 0x07
    MSP_ELRS_TLM_RATE                       = 0x08
    MSP_ELRS_BIND                           = 0x09
    MSP_ELRS_MODEL_ID                       = 0x0A
    MSP_ELRS_REQU_VTX_PKT                   = 0x0B
    MSP_ELRS_SET_TX_BACKPACK_WIFI_MODE      = 0x0C
    MSP_ELRS_SET_VRX_BACKPACK_WIFI_MODE     = 0x0D
    MSP_ELRS_SET_RX_WIFI_MODE               = 0x0E
    MSP_ELRS_SET_RX_LOAN_MODE               = 0x0F
    MSP_ELRS_GET_BACKPACK_VERSION           = 0x10
    MSP_ELRS_BACKPACK_CRSF_TLM              = 0x11
    MSP_ELRS_SET_SEND_UID                   = 0x00B5
    MSP_ELRS_SET_OSD                        = 0x00B6

    # CRSF encapsulated msp defines
    ENCAPSULATED_MSP_PAYLOAD_SIZE           = 4
    ENCAPSULATED_MSP_FRAME_LEN              = 8

    # ELRS backpack protocol opcodes
    # See: https:#docs.google.com/document/d/1u3c7OTiO4sFL2snI-hIo-uRSLfgBK4h16UrbA08Pd6U/edit#heading=h.1xw7en7jmvsj

    # outgoing, packets originating from the backpack or forwarded from the TX backpack to the VRx
    MSP_ELRS_BACKPACK_GET_CHANNEL_INDEX     = 0x0300
    MSP_ELRS_BACKPACK_SET_CHANNEL_INDEX     = 0x0301
    MSP_ELRS_BACKPACK_GET_FREQUENCY         = 0x0302
    MSP_ELRS_BACKPACK_SET_FREQUENCY         = 0x0303
    MSP_ELRS_BACKPACK_GET_RECORDING_STATE   = 0x0304
    MSP_ELRS_BACKPACK_SET_RECORDING_STATE   = 0x0305
    MSP_ELRS_BACKPACK_GET_VRX_MODE          = 0x0306
    MSP_ELRS_BACKPACK_SET_VRX_MODE          = 0x0307
    MSP_ELRS_BACKPACK_GET_RSSI              = 0x0308
    MSP_ELRS_BACKPACK_GET_BATTERY_VOLTAGE   = 0x0309
    MSP_ELRS_BACKPACK_GET_FIRMWARE          = 0x030A
    MSP_ELRS_BACKPACK_SET_BUZZER            = 0x030B
    MSP_ELRS_BACKPACK_SET_OSD_ELEMENT       = 0x030C
    MSP_ELRS_BACKPACK_SET_HEAD_TRACKING     = 0x030D  # enable/disable head-tracking forwarding packets to the TX
    MSP_ELRS_BACKPACK_SET_RTC               = 0x030E

    # incoming, packets originating from the VRx
    MSP_ELRS_BACKPACK_SET_MODE              = 0x0380  # enable wifi/binding mode
    MSP_ELRS_BACKPACK_GET_VERSION           = 0x0381  # get the bacpack firmware version
    MSP_ELRS_BACKPACK_GET_STATUS            = 0x0382  # get the status of the backpack
    MSP_ELRS_BACKPACK_SET_PTR               = 0x0383  # forwarded back to TX backpack