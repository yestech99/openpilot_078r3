from cereal import car
from selfdrive.car.hyundai.values import DBC, STEER_THRESHOLD, FEATURES, CAR, EV_HYBRID
from selfdrive.car.interfaces import CarStateBase
from opendbc.can.parser import CANParser
from selfdrive.config import Conversions as CV
from selfdrive.car.hyundai.spdcontroller  import SpdController
from selfdrive.car.hyundai.values import Buttons

GearShifter = car.CarState.GearShifter


class CarState(CarStateBase):
  def __init__(self, CP):
    super().__init__(CP)

    #Auto detection for setup
    #self.no_radar = CP.sccBus == -1
    self.mdps_bus = CP.mdpsBus
    self.sas_bus = CP.sasBus
    self.scc_bus = CP.sccBus

    self.cruise_main_button = False
    self.cruise_buttons = False

    self.lkas_button_on = False
    self.lkas_error = False    

    self.prev_cruise_main_button = False
    self.prev_cruise_buttons = False

    self.main_on = False
    self.acc_active = False
    self.cruiseState_modeSel = 0

    self.driverAcc_time = 0

    # BSM
    self.leftBlindspot_time = 0
    self.rightBlindspot_time = 0

    # blinker
    self.left_blinker_flash = 0
    self.right_blinker_flash = 0  
    self.TSigLHSw = 0
    self.TSigRHSw = 0

    self.SC = SpdController()

  def update(self, cp, cp2, cp_cam):
    cp_mdps = cp2 if self.mdps_bus else cp
    cp_sas = cp2 if self.sas_bus else cp
    cp_scc = cp2 if self.scc_bus == 1 else cp_cam if self.scc_bus == 2 else cp

    self.prev_cruise_main_button = self.cruise_main_button
    self.prev_cruise_buttons = self.cruise_buttons

    ret = car.CarState.new_message()

    ret.doorOpen = any([cp.vl["CGW1"]['CF_Gway_DrvDrSw'], cp.vl["CGW1"]['CF_Gway_AstDrSw'],
                        cp.vl["CGW2"]['CF_Gway_RLDrSw'], cp.vl["CGW2"]['CF_Gway_RRDrSw']])

    ret.seatbeltUnlatched = cp.vl["CGW1"]['CF_Gway_DrvSeatBeltSw'] == 0

    ret.wheelSpeeds.fl = cp.vl["WHL_SPD11"]['WHL_SPD_FL'] * CV.KPH_TO_MS
    ret.wheelSpeeds.fr = cp.vl["WHL_SPD11"]['WHL_SPD_FR'] * CV.KPH_TO_MS
    ret.wheelSpeeds.rl = cp.vl["WHL_SPD11"]['WHL_SPD_RL'] * CV.KPH_TO_MS
    ret.wheelSpeeds.rr = cp.vl["WHL_SPD11"]['WHL_SPD_RR'] * CV.KPH_TO_MS
    ret.vEgoRaw = (ret.wheelSpeeds.fl + ret.wheelSpeeds.fr + ret.wheelSpeeds.rl + ret.wheelSpeeds.rr) / 4.
    ret.vEgo, ret.aEgo = self.update_speed_kf(ret.vEgoRaw)

    ret.vEgo = cp.vl["CLU11"]["CF_Clu_Vanz"] * CV.KPH_TO_MS

    ret.standstill = ret.vEgoRaw < 0.1

    ret.steeringAngle = cp_sas.vl["SAS11"]['SAS_Angle']
    ret.steeringRate = cp_sas.vl["SAS11"]['SAS_Speed']
    ret.yawRate = cp.vl["ESP12"]['YAW_RATE']
    ret.steeringTorque = cp_mdps.vl["MDPS12"]['CR_Mdps_StrColTq']
    ret.steeringTorqueEps = cp_mdps.vl["MDPS12"]['CR_Mdps_OutTq']
    ret.steeringPressed = abs(ret.steeringTorque) > STEER_THRESHOLD
    ret.steerWarning = cp_mdps.vl["MDPS12"]['CF_Mdps_ToiUnavail'] != 0

    ret.leftBlinker, ret.rightBlinker = self.update_blinker(cp)

    self.VSetDis = cp_scc.vl["SCC11"]['VSetDis']
    self.clu_Vanz = cp.vl["CLU11"]["CF_Clu_Vanz"]
    self.lead_distance = cp_scc.vl["SCC11"]['ACC_ObjDist']
    lead_objspd = cp_scc.vl["SCC11"]['ACC_ObjRelSpd']
    self.lead_objspd = lead_objspd * CV.MS_TO_KPH
    self.Mdps_ToiUnavail = cp_mdps.vl["MDPS12"]['CF_Mdps_ToiUnavail']
    
    # cruise state
    #ret.cruiseState.available = True
    #ret.cruiseState.enabled = cp_scc.vl["SCC12"]['ACCMode'] != 0
    self.main_on = (cp_scc.vl["SCC11"]["MainMode_ACC"] != 0)  # 1056
    self.acc_active = (cp_scc.vl["SCC12"]['ACCMode'] != 0)    # 1057
    self.update_atom( cp, cp2, cp_cam )

    ret.cruiseState.available = self.main_on and self.cruiseState_modeSel != 4
    ret.cruiseState.enabled =  ret.cruiseState.available
    ret.cruiseState.standstill = cp_scc.vl["SCC11"]['SCCInfoDisplay'] == 4.
    
    self.is_set_speed_in_mph = int(cp.vl["CLU11"]["CF_Clu_SPEED_UNIT"])

    self.cruiseState_modeSel , speed_kph = self.SC.update_cruiseSW( self )
    ret.cruiseState.modeSel = self.cruiseState_modeSel
    if self.acc_active:
      speed_conv = CV.MPH_TO_MS if self.is_set_speed_in_mph else CV.KPH_TO_MS
      ret.cruiseState.speed = speed_kph * speed_conv
    else:
      ret.cruiseState.speed = 0

    # TODO: Find brake pressure
    ret.brake = 0
    ret.brakePressed = cp.vl["TCS13"]['DriverBraking'] != 0

    # TODO: Check this
    ret.brakeLights = bool(cp.vl["TCS13"]['BrakeLight'] or ret.brakePressed)

    if self.CP.carFingerprint in EV_HYBRID:
      ret.gas = cp.vl["E_EMS11"]['Accel_Pedal_Pos'] / 256.
      ret.gasPressed = ret.gas > 0
    else:
      ret.gas = cp.vl["EMS12"]['PV_AV_CAN'] / 100
      ret.gasPressed = bool(cp.vl["EMS16"]["CF_Ems_AclAct"])

    # TODO: refactor gear parsing in function
    # Gear Selection via Cluster - For those Kia/Hyundai which are not fully discovered, we can use the Cluster Indicator for Gear Selection,
    # as this seems to be standard over all cars, but is not the preferred method.
    ret.gearShifter = self.get_gearShifter(cp)


    if self.CP.carFingerprint in FEATURES["use_fca"]:
      ret.stockAeb = cp.vl["FCA11"]['FCA_CmdAct'] != 0
      ret.stockFcw = cp.vl["FCA11"]['CF_VSM_Warn'] == 2
    else:
      ret.stockAeb = cp_scc.vl["SCC12"]['AEB_CmdAct'] != 0
      ret.stockFcw = cp_scc.vl["SCC12"]['CF_VSM_Warn'] == 2

    ret.leftBlindspot, ret.rightBlindspot = self.get_BSM(cp)

    # save the entire LKAS11 and CLU11
    self.lkas11 = cp_cam.vl["LKAS11"]
    self.clu11 = cp.vl["CLU11"]
    self.mdps12 = cp_mdps.vl["MDPS12"]
    self.park_brake = cp.vl["CGW1"]['CF_Gway_ParkBrakeSw']
    self.steer_state = cp_mdps.vl["MDPS12"]['CF_Mdps_ToiActive']  # 0 NOT ACTIVE, 1 ACTIVE
    self.lead_distance = cp_scc.vl["SCC11"]['ACC_ObjDist']

    return ret

  def update_blinker(self, cp):
    self.TSigLHSw = cp.vl["CGW1"]['CF_Gway_TSigLHSw']
    self.TSigRHSw = cp.vl["CGW1"]['CF_Gway_TSigRHSw']
    leftBlinker = cp.vl["CGW1"]['CF_Gway_TurnSigLh'] != 0
    rightBlinker = cp.vl["CGW1"]['CF_Gway_TurnSigRh'] != 0

    if leftBlinker and not rightBlinker:
      self.left_blinker_flash = 100
      self.right_blinker_flash = 0
    elif rightBlinker and not leftBlinker:
      self.right_blinker_flash = 100
      self.left_blinker_flash = 0
    elif leftBlinker and rightBlinker:
      self.left_blinker_flash = 100
      self.right_blinker_flash = 100

    if  self.left_blinker_flash:
      self.left_blinker_flash -= 1
    if  self.right_blinker_flash:
      self.right_blinker_flash -= 1

    leftBlinker = self.left_blinker_flash != 0
    rightBlinker = self.right_blinker_flash != 0
    return  leftBlinker, rightBlinker

  def update_atom(self, cp, cp2, cp_cam):
    # atom append
    self.driverOverride = cp.vl["TCS13"]["DriverOverride"]     # 1 Acc,  2 bracking, 0 Normal
    self.cruise_main_button = cp.vl["CLU11"]["CF_Clu_CruiseSwMain"]
    self.cruise_buttons = cp.vl["CLU11"]["CF_Clu_CruiseSwState"]         # clu_CruiseSwState
    self.Lkas_LdwsSysState = cp_cam.vl["LKAS11"]["CF_Lkas_LdwsSysState"]
    self.lkas_error = self.Lkas_LdwsSysState  == 7
    if not self.lkas_error:
      self.lkas_button_on = self.Lkas_LdwsSysState 

    if self.driverOverride == 1:
      self.driverAcc_time = 100
    elif self.driverAcc_time:
      self.driverAcc_time -= 1


  def get_gearShifter(self, cp):
    gearShifter = GearShifter.unknown 
    # TODO: refactor gear parsing in function
    # Gear Selection via Cluster - For those Kia/Hyundai which are not fully discovered, we can use the Cluster Indicator for Gear Selection, as this seems to be standard over all cars, but is not the preferred method.

    if self.CP.carFingerprint in FEATURES["use_cluster_gears"]:
      if cp.vl["CLU15"]["CF_Clu_InhibitD"] == 1:
        gearShifter = GearShifter.drive
      elif cp.vl["CLU15"]["CF_Clu_InhibitN"] == 1:
        gearShifter = GearShifter.neutral
      elif cp.vl["CLU15"]["CF_Clu_InhibitP"] == 1:
        gearShifter = GearShifter.park
      elif cp.vl["CLU15"]["CF_Clu_InhibitR"] == 1:
        gearShifter = GearShifter.reverse

    # Gear Selecton via TCU12
    elif self.CP.carFingerprint in FEATURES["use_tcu_gears"]:
      gear = cp.vl["TCU12"]["CUR_GR"]
      if gear == 0:
        gearShifter = GearShifter.park
      elif gear == 14:
        gearShifter = GearShifter.reverse
      elif gear > 0 and gear < 9:    # unaware of anything over 8 currently
        gearShifter = GearShifter.drive

    # Gear Selecton - This is only compatible with optima hybrid 2017
    elif self.CP.carFingerprint in FEATURES["use_elect_gears"]:
      gear = cp.vl["ELECT_GEAR"]["Elect_Gear_Shifter"]
      if gear in (5, 8):  # 5: D, 8: sport mode
        gearShifter = GearShifter.drive
      elif gear == 6:
        gearShifter = GearShifter.neutral
      elif gear == 0:
        gearShifter = GearShifter.park
      elif gear == 7:
        gearShifter = GearShifter.reverse

    # Gear Selecton - This is not compatible with all Kia/Hyundai's, But is the best way for those it is compatible with
    else:
      gear = cp.vl["LVR12"]["CF_Lvr_Gear"]
      if gear in (5, 8):  # 5: D, 8: sport mode
        gearShifter = GearShifter.drive
      elif gear == 6:
        gearShifter = GearShifter.neutral
      elif gear == 0:
        gearShifter = GearShifter.park
      elif gear == 7:
        gearShifter = GearShifter.reverse

    return gearShifter


  def get_BSM(self, cp):
    leftBlindspot = False
    rightBlindspot = False
    if self.CP.carFingerprint in FEATURES["use_bsm"]:
      leftBlindspot = cp.vl["LCA11"]["CF_Lca_IndLeft"] != 0
      rightBlindspot = cp.vl["LCA11"]["CF_Lca_IndRight"] != 0

    if leftBlindspot != 0:
      self.leftBlindspot_time = 200
    elif self.leftBlindspot_time:
      self.leftBlindspot_time -=  1

    if rightBlindspot != 0:
      self.rightBlindspot_time = 200
    elif self.rightBlindspot_time:
      self.rightBlindspot_time -= 1

    leftBlindspot = self.leftBlindspot_time != 0
    rightBlindspot = self.rightBlindspot_time != 0

    return  leftBlindspot, rightBlindspot


  @staticmethod
  def get_parser_gears(CP, signals, checks):
    if CP.carFingerprint in FEATURES["use_cluster_gears"]:
      signals += [
        ("CF_Clu_InhibitD", "CLU15", 0),
        ("CF_Clu_InhibitP", "CLU15", 0),
        ("CF_Clu_InhibitN", "CLU15", 0),
        ("CF_Clu_InhibitR", "CLU15", 0),
      ]
      checks += [
        ("CLU15", 5)
      ]
    elif CP.carFingerprint in FEATURES["use_tcu_gears"]:
      signals += [
        ("CUR_GR", "TCU12", 0)
      ]
      checks += [
        ("TCU12", 100)
      ]
    elif CP.carFingerprint in FEATURES["use_elect_gears"]:
      signals += [("Elect_Gear_Shifter", "ELECT_GEAR", 0)]
      checks += [("ELECT_GEAR", 20)]
    else:
      signals += [
        ("CF_Lvr_Gear", "LVR12", 0)
      ]
      checks += [
        ("LVR12", 100)
      ]

    return signals, checks


  @staticmethod
  def get_parser_ev_hybrid(CP, signals, checks):
    if CP.carFingerprint in EV_HYBRID:
      signals += [
        ("Accel_Pedal_Pos", "E_EMS11", 0),
      ]
      checks += [
        ("E_EMS11", 50),
      ]
    else:
      signals += [
        ("PV_AV_CAN", "EMS12", 0),
        ("CF_Ems_AclAct", "EMS16", 0),
      ]
      checks += [
        ("EMS12", 100), 
        ("EMS16", 100),  # 608
      ]

    return signals, checks    



  @staticmethod
  def get_can_parser(CP):
    signals = [
      # sig_name, sig_address, default
      ("WHL_SPD_FL", "WHL_SPD11", 0),
      ("WHL_SPD_FR", "WHL_SPD11", 0),
      ("WHL_SPD_RL", "WHL_SPD11", 0),
      ("WHL_SPD_RR", "WHL_SPD11", 0),

      ("YAW_RATE", "ESP12", 0),

      ("CF_Gway_DrvSeatBeltInd", "CGW4", 1),

      ("CF_Gway_DrvSeatBeltSw", "CGW1", 0),
      ("CF_Gway_DrvDrSw", "CGW1", 0),       # Driver Door
      ("CF_Gway_AstDrSw", "CGW1", 0),       # Passenger door
      ("CF_Gway_RLDrSw", "CGW2", 0),        # Rear reft door
      ("CF_Gway_RRDrSw", "CGW2", 0),        # Rear right door
      ("CF_Gway_TSigLHSw", "CGW1", 0),
      ("CF_Gway_TurnSigLh", "CGW1", 0),
      ("CF_Gway_TSigRHSw", "CGW1", 0),
      ("CF_Gway_TurnSigRh", "CGW1", 0),
      ("CF_Gway_ParkBrakeSw", "CGW1", 0),

      ("CYL_PRES", "ESP12", 0),

      ("CF_Clu_CruiseSwState", "CLU11", 0),
      ("CF_Clu_CruiseSwMain", "CLU11", 0),
      ("CF_Clu_SldMainSW", "CLU11", 0),
      ("CF_Clu_ParityBit1", "CLU11", 0),
      ("CF_Clu_VanzDecimal" , "CLU11", 0),
      ("CF_Clu_Vanz", "CLU11", 0),
      ("CF_Clu_SPEED_UNIT", "CLU11", 0),
      ("CF_Clu_DetentOut", "CLU11", 0),
      ("CF_Clu_RheostatLevel", "CLU11", 0),
      ("CF_Clu_CluInfo", "CLU11", 0),
      ("CF_Clu_AmpInfo", "CLU11", 0),
      ("CF_Clu_AliveCnt1", "CLU11", 0),

      ("ACCEnable", "TCS13", 0),
      ("BrakeLight", "TCS13", 0),
      ("DriverBraking", "TCS13", 0),
      ("DriverOverride", "TCS13", 0),

      ("ESC_Off_Step", "TCS15", 0),

      ("CF_Lvr_GearInf", "LVR11", 0),        # Transmission Gear (0 = N or P, 1-8 = Fwd, 14 = Rev)


      ("MainMode_ACC", "SCC11", 0),
      ("VSetDis", "SCC11", 0),
      ("SCCInfoDisplay", "SCC11", 0),
      ("ACC_ObjDist", "SCC11", 0),
      ("ACC_ObjRelSpd", "SCC11", 0),
      ("ACCMode", "SCC12", 1),


      #("LFA_USM", "LFAHDA_MFC", 0),
      #("LFA_SysWarning", "LFAHDA_MFC", 0),
      #("ACTIVE2", "LFAHDA_MFC", 0),
      #("HDA_USM", "LFAHDA_MFC", 0),
      #("ACTIVE", "LFAHDA_MFC", 0),
    ]

    checks = [
      # address, frequency
      ("TCS13", 50),   # 916
      ("TCS15", 10),
      ("CLU11", 50),
      ("ESP12", 100),
      ("CGW1", 10),
      ("CGW4", 5),
      ("WHL_SPD11", 50),  # 902
      ("SCC11", 50),
      ("SCC12", 50),
    ]
    if not CP.mdpsBus:
      signals += [
        ("CR_Mdps_StrColTq", "MDPS12", 0),
        ("CF_Mdps_Def", "MDPS12", 0),
        ("CF_Mdps_ToiActive", "MDPS12", 0),
        ("CF_Mdps_ToiUnavail", "MDPS12", 0),
        ("CF_Mdps_MsgCount2", "MDPS12", 0),
        ("CF_Mdps_Chksum2", "MDPS12", 0),
        ("CF_Mdps_ToiFlt", "MDPS12", 0),
        ("CF_Mdps_SErr", "MDPS12", 0),
        ("CR_Mdps_StrTq", "MDPS12", 0),
        ("CF_Mdps_FailStat", "MDPS12", 0),
        ("CR_Mdps_OutTq", "MDPS12", 0)
      ]
      checks += [
        ("MDPS12", 50)
      ]
    if not CP.sasBus:
      signals += [
        ("SAS_Angle", "SAS11", 0),
        ("SAS_Speed", "SAS11", 0),
      ]
      checks += [
        ("SAS11", 100)
      ]
    if CP.sccBus == -1:
      signals += [
        ("CRUISE_LAMP_M", "EMS16", 0),
        ("CF_Lvr_CruiseSet", "LVR12", 0),
      ]

    signals, checks = CarState.get_parser_ev_hybrid( CP, signals, checks )
    signals, checks = CarState.get_parser_gears( CP, signals, checks )

    if CP.carFingerprint in FEATURES["use_bsm"]:
      signals += [
        ("CF_Lca_IndLeft", "LCA11", 0),
        ("CF_Lca_IndRight", "LCA11", 0),
      ]
      checks += [("LCA11", 50)]


    if CP.carFingerprint in FEATURES["use_fca"]:
      signals += [
        ("FCA_CmdAct", "FCA11", 0),
        ("CF_VSM_Warn", "FCA11", 0),
      ]
      checks += [("FCA11", 50)]
    else:
      signals += [
        ("AEB_CmdAct", "SCC12", 0),
        ("CF_VSM_Warn", "SCC12", 0),
      ]

    return CANParser(DBC[CP.carFingerprint]['pt'], signals, checks, 0)

  @staticmethod
  def get_can2_parser(CP):
    signals = []
    checks = []
    if CP.mdpsBus == 1:
      signals += [
        ("CR_Mdps_StrColTq", "MDPS12", 0),
        ("CF_Mdps_Def", "MDPS12", 0),
        ("CF_Mdps_ToiActive", "MDPS12", 0),
        ("CF_Mdps_ToiUnavail", "MDPS12", 0),
        ("CF_Mdps_MsgCount2", "MDPS12", 0),
        ("CF_Mdps_Chksum2", "MDPS12", 0),
        ("CF_Mdps_ToiFlt", "MDPS12", 0),
        ("CF_Mdps_SErr", "MDPS12", 0),
        ("CR_Mdps_StrTq", "MDPS12", 0),
        ("CF_Mdps_FailStat", "MDPS12", 0),
        ("CR_Mdps_OutTq", "MDPS12", 0)
      ]
      checks += [
        ("MDPS12", 50)
      ]
    if CP.sasBus == 1:
      signals += [
        ("SAS_Angle", "SAS11", 0),
        ("SAS_Speed", "SAS11", 0),
      ]
      checks += [
        ("SAS11", 100)
      ]
    return CANParser(DBC[CP.carFingerprint]['pt'], signals, checks, 1)

  @staticmethod
  def get_cam_can_parser(CP):

    signals = [
      # sig_name, sig_address, default
      ("CF_Lkas_Bca_R", "LKAS11", 0),
      ("CF_Lkas_LdwsSysState", "LKAS11", 0),
      ("CF_Lkas_SysWarning", "LKAS11", 0),
      ("CF_Lkas_LdwsLHWarning", "LKAS11", 0),
      ("CF_Lkas_LdwsRHWarning", "LKAS11", 0),
      ("CF_Lkas_HbaLamp", "LKAS11", 0),
      ("CF_Lkas_FcwBasReq", "LKAS11", 0),
      ("CF_Lkas_ToiFlt", "LKAS11", 0),  #  append      
      ("CF_Lkas_HbaSysState", "LKAS11", 0),
      ("CF_Lkas_FcwOpt", "LKAS11", 0),
      ("CF_Lkas_HbaOpt", "LKAS11", 0),
      ("CF_Lkas_FcwSysState", "LKAS11", 0),
      ("CF_Lkas_FcwCollisionWarning", "LKAS11", 0),
      ("CF_Lkas_MsgCount", "LKAS11", 0),  #  append
      ("CF_Lkas_FusionState", "LKAS11", 0),
      ("CF_Lkas_FcwOpt_USM", "LKAS11", 0),
      ("CF_Lkas_LdwsOpt_USM", "LKAS11", 0)
    ]

    checks = []

    return CANParser(DBC[CP.carFingerprint]['pt'], signals, checks, 2)
