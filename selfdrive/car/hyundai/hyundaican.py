import crcmod
import copy
from common.params import Params
from selfdrive.car.hyundai.values import CAR, CHECKSUM

hyundai_checksum = crcmod.mkCrcFun(0x11D, initCrc=0xFD, rev=False, xorOut=0xdf)


def create_lkas11(packer, frame, car_fingerprint, apply_steer, steer_req,
                  lkas11, sys_warning, sys_state, CC, enabled, bus):

  values = copy.deepcopy( lkas11 )
  values["CF_Lkas_LdwsSysState"] = 3 if enabled else 1
  #values["CF_Lkas_LdwsSysState"] = sys_state
  values["CF_Lkas_SysWarning"] = 3 if sys_warning else 0
  #values["CF_Lkas_LdwsLHWarning"] = left_lane_depart
  #values["CF_Lkas_LdwsRHWarning"] = right_lane_depart
  values["CR_Lkas_StrToqReq"] = apply_steer
  values["CF_Lkas_ActToi"] = steer_req
  values["CF_Lkas_ToiFlt"] = 0
  values["CF_Lkas_MsgCount"] = frame % 0x10
  values["CF_Lkas_Chksum"] = 0

  if car_fingerprint in [CAR.PALISADE, CAR.SANTAFE]:
    values["CF_Lkas_Bca_R"] = int(CC.hudControl.leftLaneVisible) + (int(CC.hudControl.rightLaneVisible) << 1)
    values["CF_Lkas_LdwsOpt_USM"] = 2

    # FcwOpt_USM 5 = Orange blinking car + lanes
    # FcwOpt_USM 4 = Orange car + lanes
    # FcwOpt_USM 3 = Green blinking car + lanes
    # FcwOpt_USM 2 = Green car + lanes
    # FcwOpt_USM 1 = White car + lanes
    # FcwOpt_USM 0 = No car + lanes
    values["CF_Lkas_FcwOpt_USM"] = 2 if enabled else 1

    # SysWarning 4 = keep hands on wheel
    # SysWarning 5 = keep hands on wheel (red)
    # SysWarning 6 = keep hands on wheel (red) + beep
    # Note: the warning is hidden while the blinkers are on
    values["CF_Lkas_SysWarning"] = 4 if sys_warning else 0

  if car_fingerprint == CAR.GENESIS:
    # This field is actually LdwsActivemode
    # Genesis and Optima fault when forwarding while engaged
    values["CF_Lkas_Bca_R"] = 2
    
  params = Params()
  ldws_car_fix = params.get("LdwsCarFix", encoding='utf8') == "1"
  if ldws_car_fix:
  	values["CF_Lkas_LdwsOpt_USM"] = 3
	


  dat = packer.make_can_msg("LKAS11", 0, values)[2]

  if car_fingerprint in CHECKSUM["crc8"]:
    # CRC Checksum as seen on 2019 Hyundai Santa Fe
    dat = dat[:6] + dat[7:8]
    checksum = hyundai_checksum(dat)
  elif car_fingerprint in CHECKSUM["6B"]:
    # Checksum of first 6 Bytes, as seen on 2018 Kia Sorento
    checksum = sum(dat[:6]) % 256
  else:
    # Checksum of first 6 Bytes and last Byte as seen on 2018 Kia Stinger
    checksum = (sum(dat[:6]) + dat[7]) % 256

  values["CF_Lkas_Chksum"] = checksum

  return packer.make_can_msg("LKAS11", bus, values)


def create_clu11(packer, frame, bus, clu11, button, speed = None):
  values = copy.deepcopy( clu11 )
  if speed != None:
    values["CF_Clu_Vanz"] = speed

  values["CF_Clu_CruiseSwState"] = button
  values["CF_Clu_AliveCnt1"] = frame % 0x10
  return packer.make_can_msg("CLU11", bus, values)


def create_lfa_mfa(packer, frame, enabled):
  values = {
    "ACTIVE": enabled,
  }

  # ACTIVE 1 = Green steering wheel icon

  # LFA_USM 2 & 3 = LFA cancelled, fast loud beeping
  # LFA_USM 0 & 1 = No mesage

  # LFA_SysWarning 1 = "Switching to HDA", short beep
  # LFA_SysWarning 2 = "Switching to Smart Cruise control", short beep
  # LFA_SysWarning 3 =  LFA error

  # ACTIVE2: nothing
  # HDA_USM: nothing

  return packer.make_can_msg("LFAHDA_MFC", 0, values)

  
def create_mdps12(packer, frame, mdps12):
  values = copy.deepcopy( mdps12 )
  #values = mdps12
  values["CF_Mdps_ToiActive"] = 0
  values["CF_Mdps_ToiUnavail"] = 1
  values["CF_Mdps_MsgCount2"] = frame % 0x100
  values["CF_Mdps_Chksum2"] = 0

  dat = packer.make_can_msg("MDPS12", 2, values)[2]
  checksum = sum(dat) % 256
  values["CF_Mdps_Chksum2"] = checksum

  return packer.make_can_msg("MDPS12", 2, values)
