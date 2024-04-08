from enum import Enum

#
# Plugin Supported Hardware Settings 
#

class hardwareOptions(Enum):
    NONE = 'none'
    HDZERO = 'hdzero'

HARDWARE_SETTINGS = {
    'hdzero' : {
        'column_size'   : 18,
        'row_size'      : 50,
    }
}

