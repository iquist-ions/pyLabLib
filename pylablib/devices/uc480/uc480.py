from . import uc480_defs
from .uc480_lib import lib, uc480Error, uc480LibError

from ...core.utils import py3
from ...core.devio import interface
from ..interface import camera

import numpy as np
import collections
import ctypes



class uc480TimeoutError(uc480Error):
    "uc480 frame timeout error"


TCameraInfo=collections.namedtuple("TCameraInfo",["cam_id","dev_id","sens_id","model","serial_number","in_use","status"])
def list_cameras():
    """List camera connections (interface kind and camera index)"""
    lib.initlib()
    return [TCameraInfo(ci.dwCameraID,ci.dwDeviceID,ci.dwSensorID,py3.as_str(ci.Model),py3.as_str(ci.SerNo),bool(ci.dwInUse),ci.dwStatus)
         for ci in lib.is_GetCameraList()]
def get_cameras_number():
    """Get the total number of connected uc480 cameras"""
    return len(list_cameras())


TDeviceInfo=collections.namedtuple("TDeviceInfo",["model","manufacturer","serial_number","usb_version","date","dll_version","camera_type"])
TAcquiredFramesStatus=collections.namedtuple("TAcquiredFramesStatus",["acquired","transfer_missed"])
class UC480Camera(camera.IBinROICamera,camera.IExposureCamera):
    """
    Thorlabs uc480 camera.

    Args:
        cam_id(int): camera ID; use 0 to get the first available camera
        roi_binning_mode: determines whether binning in ROI refers to binning or subsampling;
            can be ``"bin"``, ``"subsample"``, or ``"auto"`` (since most cameras only support one, it will pick the one which has non-trivial value, or ``"bin"`` if both are available).
        dev_id(int): if ``None`` use `cam_id` as a camera id (``dwCameraID`` field of the camera info returned by :func:`get_cameras_list`);
            otherwise, ignore value of `cam_id` and use `dev_id` as device id (``dwDeviceID`` field of the camera info).
            The first method requires assigning camera IDs beforehand (otherwise IDs might overlap, in which case only one camera can be accessed),
            but the assigned IDs are permanent; the second method always has unique IDs, but they might change if the cameras are disconnected and reconnected.
            For a more reliable assignment, one can use :func:`find_device_id` function to find device ID based on the camera serial number.
    """
    Error=uc480Error
    TimeoutError=uc480TimeoutError
    def __init__(self, cam_id=0, roi_binning_mode="auto", dev_id=None):
        super().__init__()
        lib.initlib()
        if dev_id is None:
            self.id=cam_id
            self.is_dev_id=False
        else:
            self.id=dev_id
            self.is_dev_id=True
        self.hcam=None
        self._buffers=None
        self._acq_in_progress=True
        self.open()
        self._all_color_modes=self._check_all_color_modes()
        self._set_auto_mono_color_mode()
        if roi_binning_mode=="auto":
            if self.get_supported_binning_modes()==([1],[1]):
                roi_binning_mode="subsample"
            else:
                roi_binning_mode="bin"
        self._roi_binning_mode=roi_binning_mode

        self._add_info_variable("device_info",self.get_device_info)
        self._add_settings_variable("subsampling",self.get_subsampling,self.set_subsampling)
        self._add_info_variable("subsampling_modes",self.get_supported_subsampling_modes)
        self._add_settings_variable("binning",self.get_binning,self.set_binning)
        self._add_info_variable("binning_modes",self.get_supported_binning_modes)
        self._add_settings_variable("pixel_rate",self.get_pixel_rate,self.set_pixel_rate)
        self._add_info_variable("pixel_rates_range",self.get_pixel_rates_range)
        self._add_info_variable("max_gains",self.get_max_gains)
        self._add_settings_variable("gains",self.get_gains,self.set_gains)
        self._add_settings_variable("gain_boost",self.get_gain_boost,self.set_gain_boost)
        self._add_status_variable("acq_frame_status",self.get_acquired_frame_status)
        self._add_info_variable("all_color_modes",self.get_all_color_modes)
        self._add_settings_variable("color_mode",self.get_color_mode,self.set_color_mode)

    def open(self):
        """Open connection to the camera"""
        if self.hcam is None:
            self.hcam=lib.is_InitCamera(None,self.id|(uc480_defs.DEVENUM.IS_USE_DEVICE_ID if self.is_dev_id else 0))
    def close(self):
        """Close connection to the camera"""
        if self.hcam is not None:
            lib.is_ExitCamera(self.hcam)
            self.hcam=None
        self.hcam=None
    def is_opened(self):
        """Check if the device is connected"""
        return self.hcam is not None

    def get_device_info(self):
        """
        Get camera model data.

        Return tuple ``(model, manufacturer, serial_number, usb_version, date, dll_version, camera_type)``.
        """
        sen_info=self._get_sensor_info()
        cam_info=lib.is_GetCameraInfo(self.hcam)
        dll_ver=lib.is_GetDLLVersion()
        dll_ver="{}.{}.{}".format((dll_ver>>24),(dll_ver>>16)&0xFF,dll_ver&0xFFFF)
        return TDeviceInfo(py3.as_str(sen_info.strSensorName),py3.as_str(cam_info.ID),py3.as_str(cam_info.SerNo),py3.as_str(cam_info.Version),
            py3.as_str(cam_info.Date),dll_ver,cam_info.Type)
    def _get_sensor_info(self):
        return lib.is_GetSensorInfo(self.hcam)

    ### Buffer controls ###
    def _allocate_buffers(self, n):
        self._deallocate_buffers()
        frame_size=self._get_data_dimensions_rc()[::-1]
        bpp=self._get_pixel_mode_settings()[0]
        self._buffers=[]
        for _ in range(n):
            self._buffers.append(lib.is_AllocImageMem(1,frame_size[0],frame_size[1],bpp))
            lib.is_AddToSequence(self.hcam,*self._buffers[-1])
        return n
    def _deallocate_buffers(self):
        if self._buffers is not None:
            lib.is_ClearSequence(self.hcam)
            for b in self._buffers:
                lib.is_FreeImageMem(self.hcam,*b)
            self._buffers=None


    ### Generic controls ###
    def get_frame_timings(self):
        exp=lib.is_Exposure(self.hcam,uc480_defs.EXPOSURE_CMD.IS_EXPOSURE_CMD_GET_EXPOSURE,ctypes.c_double)*1E-3
        frame_rate=lib.is_SetFrameRate(self.hcam,uc480_defs.FRAMERATE.IS_GET_FRAMERATE)
        return self._TAcqTimings(exp,1./frame_rate)
    def set_exposure(self, exposure):
        """Set camera exposure"""
        exposure=max(exposure,1E-6) # exposure=0 sets it to some default value
        exposure=lib.is_Exposure(self.hcam,uc480_defs.EXPOSURE_CMD.IS_EXPOSURE_CMD_SET_EXPOSURE,ctypes.c_double,exposure*1E3)
        return exposure*1E-3
    def set_frame_period(self, frame_time):
        """Set frame period (time between two consecutive frames in the internal trigger mode)"""
        ftr=lib.is_GetFrameTimeRange(self.hcam)
        frame_time=min(max(frame_time,ftr[0]),ftr[1])
        lib.is_SetFrameRate(self.hcam,1./frame_time)
        return self.get_frame_period()
    def get_pixel_rate(self):
        """Get camera pixel rate (in Hz)"""
        return lib.is_PixelClock(self.hcam,uc480_defs.PIXELCLOCK_CMD.IS_PIXELCLOCK_CMD_GET,ctypes.c_uint)*1E6
    def get_available_pixel_rates(self):
        """Get all available pixel rates (in Hz)"""
        nrates=lib.is_PixelClock(self.hcam,uc480_defs.PIXELCLOCK_CMD.IS_PIXELCLOCK_CMD_GET_NUMBER,ctypes.c_uint)
        rates=lib.is_PixelClock(self.hcam,uc480_defs.PIXELCLOCK_CMD.IS_PIXELCLOCK_CMD_GET_LIST,ctypes.c_uint*nrates)
        return sorted([r*1E6 for r in rates])
    def get_pixel_rates_range(self):
        """
        Get range of allowed pixel rates (in Hz).

        Return tuple ``(min, max, step)`` if minimal and maximal value, and a step.
        """
        rng=lib.is_PixelClock(self.hcam,uc480_defs.PIXELCLOCK_CMD.IS_PIXELCLOCK_CMD_GET_RANGE,ctypes.c_uint*3)
        return tuple([v*1E6 for v in rng])
    def set_pixel_rate(self, rate=None):
        """
        Set camera pixel rate (in Hz)

        The rate is always rounded to the closest available.
        If `rate` is ``None``, set the maximal possible rate.
        """
        rates=self.get_available_pixel_rates()
        if rate is None:
            rate=rates[-1]
        else:
            rate=sorted(rates,key=lambda r: abs(r-rate))[0]
        lib.is_PixelClock(self.hcam,uc480_defs.PIXELCLOCK_CMD.IS_PIXELCLOCK_CMD_SET,ctypes.c_uint,int(np.round(rate/1E6)))
        return self.get_pixel_rate()

    _color_modes= { "raw8": uc480_defs.COLORMODE.IS_CM_SENSOR_RAW8, "raw10": uc480_defs.COLORMODE.IS_CM_SENSOR_RAW10,
                    "raw12": uc480_defs.COLORMODE.IS_CM_SENSOR_RAW12, "raw16": uc480_defs.COLORMODE.IS_CM_SENSOR_RAW16,
                    "mono8": uc480_defs.COLORMODE.IS_CM_MONO8, "mono10": uc480_defs.COLORMODE.IS_CM_MONO10,
                    "mono12": uc480_defs.COLORMODE.IS_CM_MONO12, "mono16": uc480_defs.COLORMODE.IS_CM_MONO16,
                    "bgr5p": uc480_defs.COLORMODE.IS_CM_BGR5_PACKED, "bgr565p": uc480_defs.COLORMODE.IS_CM_BGR565_PACKED,
                    "rgb8p": uc480_defs.COLORMODE.IS_CM_RGB8_PACKED, "bgr8p": uc480_defs.COLORMODE.IS_CM_BGR8_PACKED,
                    "rgba8p": uc480_defs.COLORMODE.IS_CM_RGBA8_PACKED, "bgra8p": uc480_defs.COLORMODE.IS_CM_BGRA8_PACKED,
                    "rgby8p": uc480_defs.COLORMODE.IS_CM_RGBY8_PACKED, "bgry8p": uc480_defs.COLORMODE.IS_CM_BGRY8_PACKED,
                    "rgb10p": uc480_defs.COLORMODE.IS_CM_RGB10_PACKED, "bgr10p": uc480_defs.COLORMODE.IS_CM_BGR10_PACKED,
                    "rgb10up": uc480_defs.COLORMODE.IS_CM_RGB10_UNPACKED, "bgr10up": uc480_defs.COLORMODE.IS_CM_BGR10_UNPACKED,
                    "rgb12up": uc480_defs.COLORMODE.IS_CM_RGB12_UNPACKED, "bgr12up": uc480_defs.COLORMODE.IS_CM_BGR12_UNPACKED,
                    "rgba12up": uc480_defs.COLORMODE.IS_CM_RGBA12_UNPACKED, "bgra12up": uc480_defs.COLORMODE.IS_CM_BGRA12_UNPACKED,
                    "cbycryp": uc480_defs.COLORMODE.IS_CM_CBYCRY_PACKED, "uyuvp": uc480_defs.COLORMODE.IS_CM_UYVY_PACKED,
                    "uyvy_monop": uc480_defs.COLORMODE.IS_CM_UYVY_MONO_PACKED, "uyuv_bayerp": uc480_defs.COLORMODE.IS_CM_UYVY_BAYER_PACKED,
                    "jpeg": uc480_defs.COLORMODE.IS_CM_JPEG, "rgb8plan": uc480_defs.COLORMODE.IS_CM_RGB8_PLANAR }
    _p_color_mode=interface.EnumParameterClass("color_mode",_color_modes)
    def _check_all_color_modes(self):
        names=[]
        m0=lib.is_SetColorMode(self.hcam,uc480_defs.COLORMODE.IS_GET_COLOR_MODE)
        for n,m in self._color_modes.items():
            try:
                lib.is_SetColorMode(self.hcam,m,check=True)
                nm=lib.is_SetColorMode(self.hcam,uc480_defs.COLORMODE.IS_GET_COLOR_MODE)
                if m==nm:
                    names.append(n)
            except uc480LibError as err:
                if err.code!=uc480_defs.ERROR.IS_INVALID_COLOR_FORMAT:
                    raise
        lib.is_SetColorMode(self.hcam,m0)
        return names
    def get_all_color_modes(self):
        """Get a list of all available color modes"""
        return self._all_color_modes
    @interface.use_parameters(_returns="color_mode")
    def get_color_mode(self):
        """
        Get current color mode.

        For possible modes, see :meth:`get_all_color_modes`.
        """
        return lib.is_SetColorMode(self.hcam,uc480_defs.COLORMODE.IS_GET_COLOR_MODE)
    @interface.use_parameters(mode="color_mode")
    def set_color_mode(self, mode):
        """
        Set current color mode.

        For possible modes, see :meth:`get_all_color_modes`.
        """
        lib.is_SetColorMode(self.hcam,mode,check=True)
        return self.get_color_mode()
    def _set_auto_mono_color_mode(self):
        """Set color mode to the most appropriate mono setting, if the sensor is mono"""
        si=self._get_sensor_info()
        if si.nColorMode==b"\x01": # monochrome
            for mode in ["mono16","mono8"]:
                try:
                    self.set_color_mode(mode)
                    return
                except uc480LibError as err:
                    if err.code!=uc480_defs.ERROR.IS_INVALID_COLOR_FORMAT:
                        raise
    _mode_properties={  "raw8":(8,1),"raw10":(16,1),"raw12":(16,1),"raw16":(16,1), # needs additional decoding
                        "mono8":(8,1),"mono10":(16,1),"mono12":(16,1),"mono16":(16,1),
                        "bgr5p":(16,1),"bgr565p":(16,1),"rgb8p":(24,3),"bgr8p":(24,3),
                        "rgba8p":(32,4),"bgra8p":(32,4),"rgby8p":(32,4),"bgry8p":(32,4),
                        "rgb10p":(24,1),"bgr10p":(24,1),"rgb10up":(48,3),"bgr10up":(48,3),
                        "rgb12up":(48,3),"bgr12up":(48,3),"rgba12up":(64,4),"bgra12up":(64,4),
                        "cbycryp":None,"uyuvp":(32,4),"uyvy_monop":(32,4),"uyuv_bayerp":(32,4),
                        "jpeg":None,"rgb8plan":None}
    def _get_pixel_mode_settings(self, mode=None):
        """
        Get pixel mode settings (bits per pixel and channels per pixel)
        
        Packed modes are assumed to be one-channel (i.e., no unpacking is done).
        """
        if mode is None:
            mode=self.get_color_mode()
        if mode not in self._color_modes:
            mode=self._p_color_mode.i(mode&0x7F)
        return self._mode_properties[mode]

    def get_gains(self):
        """
        Get current gains.

        Return tuple ``(master, red, green, blue)`` of corresponding gain factors.
        """
        return tuple([lib.is_SetHWGainFactor(self.hcam,uc480_defs.GAINFACTOR.IS_GET_MASTER_GAIN_FACTOR+i,0)/100 for i in range(4)])
    def get_max_gains(self):
        """
        Get maximal gains.

        Return tuple ``(master, red, green, blue)`` of corresponding maximal gain factors.
        """
        return tuple([lib.is_SetHWGainFactor(self.hcam,uc480_defs.GAINFACTOR.IS_INQUIRE_MASTER_GAIN_FACTOR+i,100)/100 for i in range(4)])
    def _set_channel_gain(self, i, ivalue):
        max_gain=lib.is_SetHWGainFactor(self.hcam,uc480_defs.GAINFACTOR.IS_INQUIRE_MASTER_GAIN_FACTOR+i,100)
        min_gain=100
        ivalue=max(min(ivalue,max_gain),min_gain)
        lib.is_SetHWGainFactor(self.hcam,uc480_defs.GAINFACTOR.IS_SET_MASTER_GAIN_FACTOR+i,ivalue,check=True)
    def set_gains(self, master=None, red=None, green=None, blue=None):
        """
        Set current gains.

        If supplied value is ``None``, keep it unchanged.
        """
        for i,g in enumerate([master,red,green,blue]):
            if g is not None:
                self._set_channel_gain(i,int(g*100))
        return self.get_gains()
    def get_gain_boost(self):
        """Check if gain boost is enabled"""
        return bool(lib.is_SetGainBoost(self.hcam,uc480_defs.GAIN.IS_GET_SUPPORTED_GAINBOOST) and lib.is_SetGainBoost(self.hcam,uc480_defs.GAIN.IS_GET_GAINBOOST))
    def set_gain_boost(self, enabled):
        """Enable or disable gain boost"""
        if lib.is_SetGainBoost(self.hcam,self.hcam,uc480_defs.GAIN.IS_GET_SUPPORTED_GAINBOOST):
            lib.is_SetGainBoost(self.hcam,1 if enabled else 0,check=True)
        return self.get_gain_boost()


    ### Acquisition process controls ###
    def setup_acquisition(self, nframes=100):
        """
        Setup acquisition.

        `nframes` determines number of size of the ring buffer (by default, 100).
        """
        super().setup_acquisition(nframes=nframes)
        self._allocate_buffers(n=nframes)
    def clear_acquisition(self):
        self._deallocate_buffers()
        super().clear_acquisition()
    def start_acquisition(self, *args, **kwargs):
        """
        Start camera acquisition.

        `nframes` specifies number of frames in the ring buffer (by default, 100).
        """
        self.stop_acquisition()
        super().start_acquisition(*args,**kwargs)
        lib.is_ResetCaptureStatus(self.hcam)
        lib.is_CaptureVideo(self.hcam,uc480_defs.LIVEFREEZE.IS_DONT_WAIT,check=True)
        self._acq_in_progress=True
        self._frame_counter.reset(self._acq_params["nframes"])
    def stop_acquisition(self):
        if self._acq_in_progress():
            self._frame_counter.update_acquired_frames(self._get_acquired_frames())
            lib.is_StopLiveVideo(self.hcam,0)
            self._acq_in_progress=False
    def acquisition_in_progress(self):
        return self._acq_in_progress,lib.is_CaptureVideo(self.hcam,uc480_defs.LIVEFREEZE.IS_GET_LIVE)
    def get_acquired_frame_status(self):
        acquired=self._get_acquired_frames()
        cstat=lib.is_GetCaptureStatus(self.hcam).adwCapStatusCnt_Detail
        transfer_missed=sum([cstat[i] for i in [0xa2,0xa3,0xb2,0xc7]])
        return TAcquiredFramesStatus(acquired,transfer_missed)
    def _get_acquired_frames(self):
        return lib.is_CameraStatus(self.hcam,uc480_defs.CAMINFO.IS_SEQUENCE_CNT,uc480_defs.CAMINFO.IS_GET_STATUS)


    ### Image settings and transfer controls ###
    def _truncate_subsampling(self, hsub, vsub, all_modes):
        hsub=max(hsub,1)
        vsub=max(vsub,1)
        hmodes,vmodes=all_modes
        hsub=max([m for m in hmodes if m<=hsub])
        vsub=max([m for m in vmodes if m<=vsub])
        return hsub,vsub
    _subsampling_modes= {   ("v",1):0,("h",1):0,
                            ("v",2):uc480_defs.SUBSAMPLING.IS_SUBSAMPLING_2X_VERTICAL,("h",2):uc480_defs.SUBSAMPLING.IS_SUBSAMPLING_2X_HORIZONTAL,
                            ("v",3):uc480_defs.SUBSAMPLING.IS_SUBSAMPLING_3X_VERTICAL,("h",3):uc480_defs.SUBSAMPLING.IS_SUBSAMPLING_3X_HORIZONTAL,
                            ("v",4):uc480_defs.SUBSAMPLING.IS_SUBSAMPLING_4X_VERTICAL,("h",4):uc480_defs.SUBSAMPLING.IS_SUBSAMPLING_4X_HORIZONTAL,
                            ("v",5):uc480_defs.SUBSAMPLING.IS_SUBSAMPLING_5X_VERTICAL,("h",5):uc480_defs.SUBSAMPLING.IS_SUBSAMPLING_5X_HORIZONTAL,
                            ("v",6):uc480_defs.SUBSAMPLING.IS_SUBSAMPLING_6X_VERTICAL,("h",6):uc480_defs.SUBSAMPLING.IS_SUBSAMPLING_6X_HORIZONTAL,
                            ("v",8):uc480_defs.SUBSAMPLING.IS_SUBSAMPLING_8X_VERTICAL,("h",8):uc480_defs.SUBSAMPLING.IS_SUBSAMPLING_8X_HORIZONTAL,
                            ("v",16):uc480_defs.SUBSAMPLING.IS_SUBSAMPLING_16X_VERTICAL,("h",16):uc480_defs.SUBSAMPLING.IS_SUBSAMPLING_16X_HORIZONTAL }
    _p_subsampling_mode=interface.EnumParameterClass("subsampling_mode",_subsampling_modes)
    def get_supported_subsampling_modes(self):
        """
        Get all supported subsampling modes.

        Return tuple ``(horizontal, vertical)`` of lists with all possible supported subsampling factors.
        """
        all_modes=lib.is_SetSubSampling(self.hcam,uc480_defs.SUBSAMPLING.IS_GET_SUPPORTED_SUBSAMPLING)
        supp={"v":set(),"h":set()}
        for (d,s),mask in self._subsampling_modes.items():
            if all_modes&mask==mask:
                supp[d].add(s)
        return sorted(supp["h"]),sorted(supp["v"])
    def get_subsampling(self):
        """Get current subsampling"""
        hsub=lib.is_SetSubSampling(self.hcam,uc480_defs.SUBSAMPLING.IS_GET_SUBSAMPLING_FACTOR_HORIZONTAL)
        vsub=lib.is_SetSubSampling(self.hcam,uc480_defs.SUBSAMPLING.IS_GET_SUBSAMPLING_FACTOR_VERTICAL)
        return hsub,vsub
    def set_subsampling(self, hsub=1, vsub=1):
        """
        Set subsampling.
        
        If values are not supported, get the closest value below the requested.
        Automatically turns off binning.
        """
        hsub,vsub=self._truncate_subsampling(hsub,vsub,self.get_supported_subsampling_modes())
        mask=self._p_subsampling_mode(("h",hsub))|self._p_subsampling_mode(("v",vsub))
        lib.is_SetSubSampling(self.hcam,mask,check=True)
        return self.get_subsampling()


    _binning_modes= {   ("v",1):0,("h",1):0,
                            ("v",2):uc480_defs.BINNING.IS_BINNING_2X_VERTICAL,("h",2):uc480_defs.BINNING.IS_BINNING_2X_HORIZONTAL,
                            ("v",3):uc480_defs.BINNING.IS_BINNING_3X_VERTICAL,("h",3):uc480_defs.BINNING.IS_BINNING_3X_HORIZONTAL,
                            ("v",4):uc480_defs.BINNING.IS_BINNING_4X_VERTICAL,("h",4):uc480_defs.BINNING.IS_BINNING_4X_HORIZONTAL,
                            ("v",5):uc480_defs.BINNING.IS_BINNING_5X_VERTICAL,("h",5):uc480_defs.BINNING.IS_BINNING_5X_HORIZONTAL,
                            ("v",6):uc480_defs.BINNING.IS_BINNING_6X_VERTICAL,("h",6):uc480_defs.BINNING.IS_BINNING_6X_HORIZONTAL,
                            ("v",8):uc480_defs.BINNING.IS_BINNING_8X_VERTICAL,("h",8):uc480_defs.BINNING.IS_BINNING_8X_HORIZONTAL,
                            ("v",16):uc480_defs.BINNING.IS_BINNING_16X_VERTICAL,("h",16):uc480_defs.BINNING.IS_BINNING_16X_HORIZONTAL }
    _p_binning_mode=interface.EnumParameterClass("binning_mode",_binning_modes)
    def get_supported_binning_modes(self):
        """
        Get all supported binning modes.

        Return tuple ``(horizontal, vertical)`` of lists with all possible supported binning factors.
        """
        all_modes=lib.is_SetBinning(self.hcam,uc480_defs.BINNING.IS_GET_SUPPORTED_BINNING)
        supp={"v":set(),"h":set()}
        for (d,s),mask in self._binning_modes.items():
            if all_modes&mask==mask:
                supp[d].add(s)
        return sorted(supp["v"]),sorted(supp["h"])
    def get_binning(self):
        """Get current binning"""
        hbin=lib.is_SetBinning(self.hcam,uc480_defs.BINNING.IS_GET_BINNING_FACTOR_HORIZONTAL)
        vbin=lib.is_SetBinning(self.hcam,uc480_defs.BINNING.IS_GET_BINNING_FACTOR_VERTICAL)
        return hbin,vbin
    def set_binning(self, hbin=1, vbin=1):
        """
        Set binning.
        
        If values are not supported, get the closest value below the requested.
        Automatically turns off subsampling.
        """
        hbin,vbin=self._truncate_subsampling(hbin,vbin,self.get_supported_binning_modes())
        mask=self._p_binning_mode(("h",hbin))|self._p_binning_mode(("v",vbin))
        lib.is_SetBinning(self.hcam,mask,check=True)
        return self.get_binning()

    def get_detector_size(self):
        """Get camera detector size (in pixels) as a tuple ``(width, height)``"""
        sensor=self._get_sensor_info()
        return sensor.nMaxWidth,sensor.nMaxHeight
    def _check_aoi(self, aoi):
        lib.is_AOI(self.hcam,uc480_defs.IMAGE.IS_AOI_IMAGE_SET_AOI,uc480_defs.CIS_RECT,aoi)
        return tuple(lib.is_AOI(self.hcam,uc480_defs.IMAGE.IS_AOI_IMAGE_GET_AOI,uc480_defs.CIS_RECT))==aoi
    def _get_roi_binning(self):
        return self.get_subsampling() if self._roi_binning_mode=="subsample" else self.get_binning()
    def _set_roi_binning(self, hbin, vbin):
        if self._roi_binning_mode=="subsample":
            self.set_subsampling(hbin,vbin)
            self.set_binning()
        else:
            self.set_subsampling()
            self.set_binning(hbin,vbin)
    def _truncate_roi_binning(self, hbin, vbin):
        all_modes=self.get_supported_subsampling_modes() if self._roi_binning_mode=="subsample" else self.get_supported_binning_modes()
        return self._truncate_subsampling(hbin,vbin,all_modes)
    def _get_roi_limits(self):
        smin=lib.is_AOI(self.hcam,uc480_defs.IMAGE.IS_AOI_IMAGE_GET_SIZE_MIN,uc480_defs.CIS_SIZE_2D)
        sstep=lib.is_AOI(self.hcam,uc480_defs.IMAGE.IS_AOI_IMAGE_GET_SIZE_INC,uc480_defs.CIS_SIZE_2D)
        pstep=lib.is_AOI(self.hcam,uc480_defs.IMAGE.IS_AOI_IMAGE_GET_POS_INC,uc480_defs.CIS_POINT_2D)
        return (smin[0],sstep[0],pstep[0]),(smin[1],sstep[1],pstep[1])
    def _adj_roi_axis(self, start, end, minsize, detsize, wstep, pstep, binv):
        if end is None:
            end=detsize
        start//=binv
        end//=binv
        detsize//=binv
        end=min(end,detsize)
        start=min(start,detsize)
        start-=start%pstep
        end-=end%pstep
        end-=(end-start)%wstep
        if end-start<minsize:
            end=start+minsize
        if end>detsize:
            end=detsize
            start=detsize-minsize
        return start*binv,end*binv
    def _trunc_roi(self, hstart=0, hend=None, vstart=0, vend=None, hbin=1, vbin=1):
        wdet,hdet=self.get_detector_size()
        hbin,vbin=self._truncate_roi_binning(hbin,vbin)
        hlims,vlims=self._get_roi_limits()
        hstart,hend=self._adj_roi_axis(hstart,hend,hlims[0],wdet,hlims[1],hlims[2],hbin)
        vstart,vend=self._adj_roi_axis(vstart,vend,vlims[0],hdet,vlims[1],vlims[2],vbin)
        return hstart,hend,vstart,vend,hbin,vbin
    def get_roi(self):
        """
        Get current ROI.

        Return tuple ``(hstart, hend, vstart, vend, hbin, vbin)``.
        """
        rect=lib.is_AOI(self.hcam,uc480_defs.IMAGE.IS_AOI_IMAGE_GET_AOI,uc480_defs.CIS_RECT)
        hbin,vbin=self._get_roi_binning()
        return (rect.s32X*hbin,(rect.s32X+rect.s32Width)*hbin,rect.s32Y*vbin,(rect.s32Y+rect.s32Height)*vbin,hbin,vbin)
    def set_roi(self, hstart=0, hend=None, vstart=0, vend=None, hbin=1, vbin=1):
        """
        Setup camera ROI.

        `hstart` and `hend` specify horizontal image extent, `vstart` and `vend` specify vertical image extent
        (start are inclusive, stop are exclusive, starting from 0), `hbin` and `vbin` specify binning.
        By default, all non-supplied parameters take extreme values.
        """
        roi=hstart,hend,vstart,vend,hbin,vbin
        hstart,hend,vstart,vend,hbin,vbin=self._trunc_roi(*roi)
        self._set_roi_binning(hbin,vbin)
        aoi=uc480_defs.IS_RECT(hstart//hbin,vstart//vbin,(hend-hstart)//hbin,(vend-vstart)//vbin)
        lib.is_AOI(self.hcam,uc480_defs.IMAGE.IS_AOI_IMAGE_SET_AOI,uc480_defs.CIS_RECT,aoi)
        return self.get_roi()
    def get_roi_limits(self):
        """
        Get the minimal and maximal ROI parameters.

        Return tuple ``(min_roi, max_roi)``, where each element is in turn 6-tuple describing the ROI.
        """
        wdet,hdet=self.get_detector_size()
        hlims,vlims=self._get_roi_limits()
        hbin,vbin=self._truncate_roi_binning(wdet,hdet)
        min_roi=(0,0,hlims[0],vlims[0],1,1)
        max_roi=(wdet-hlims[0],wdet-vlims[0],wdet,hdet,hbin,vbin)
        return (min_roi,max_roi)

    def _get_data_dimensions_rc(self):
        roi=self.get_roi()
        return (roi[3]-roi[2])//roi[5],(roi[1]-roi[0])//roi[4]

    
    _np_dtypes={8:"u1",16:"<u2",32:"<u4"}
    def _read_buffer(self, n):
        buff=self._buffers[n%len(self._buffers)]
        frame_info=lib.is_GetImageInfo(self.hcam,buff[1])
        bpp,nchan=self._get_pixel_mode_settings()
        shape=(frame_info.dwImageHeight,frame_info.dwImageWidth)+((nchan,) if nchan>1 else ())
        frame=np.empty(shape=shape,dtype=self._np_dtypes[bpp//nchan])
        lib.is_CopyImageMem(self.hcam,buff[0],buff[1],frame.ctypes.data)
        return frame,frame_info
    def _read_frames(self, rng, return_info=False):
        data=[self._read_buffer(n) for n in range(rng[0],rng[1])]
        return [d[0] for d in data],[d[1] for d in data]
    def _zero_frame(self, n):
        bpp,nchan=self._get_pixel_mode_settings()
        shape=self.get_data_dimensions()+((nchan,) if nchan>1 else ())
        return np.zeros((n,)+shape,dtype=self._np_dtypes[bpp//nchan])
    
    def _get_grab_acquisition_parameters(self, nframes, buff_size):
        if buff_size is None:
            buff_size=self._default_acq_params.get("nframes",100)
        return {"nframes":buff_size}