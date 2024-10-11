from pylablib.devices.Andor import AndorSDK2
from pylablib.devices.Andor.AndorSDK2 import AndorSDK2Camera, get_cameras_number as get_cameras_number_SDK2, get_SDK_version as get_SDK2_version
# from pylablib.devices.Andor import AndorSDK3
# from pylablib.devices.Andor.AndorSDK3 import AndorSDK3Camera, get_cameras_number as get_cameras_number_SDK3
from pylablib.devices.Andor.Shamrock import list_spectrographs as list_shamrock_spectrographs, get_spectrographs_number as get_shamrock_spectrographs_number, ShamrockSpectrograph
from pylablib.devices.Andor.base import AndorError, AndorNotSupportedError, AndorTimeoutError