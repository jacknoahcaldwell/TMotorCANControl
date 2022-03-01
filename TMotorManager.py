import can
import time
import os
from collections import namedtuple
from enum import Enum
from math import isfinite
import StatProfiler
from SoftRealtimeLoop import SoftRealtimeLoop

MIT_Params = {
        'AK80-9':{
            'P_min' : -12.5,
            'P_max' : 12.5,
            'V_min' : -50.0,
            'V_max' : 50.0,
            'I_min' : -18.0,
            'I_max' : 18.0,
            'Kp_min': 0.0,
            'Kp_max': 500.0,
            'Kd_min': 0.0,
            'Kd_max': 5.0,
            'NM_PER_AMP': 0.146 # probably the same if its the same motor
        }
}

# These all use rad, rad/s, rad/s/s, A, and degrees C for their values
class motor_state:
    def __init__(self,position, velocity, current, temperature, error, acceleration):
        self.position = position
        self.velocity = velocity
        self.current = current
        self.temperature = temperature
        self.error = error
        self.acceleration = acceleration

    def set_state(self, other_motor_state):
        self.position = other_motor_state.position
        self.velocity = other_motor_state.velocity
        self.current = other_motor_state.current
        self.temperature = other_motor_state.temperature
        self.error = other_motor_state.error
        self.acceleration = other_motor_state.acceleration

MIT_motor_state = namedtuple('motor_state', 'position velocity current temperature error')
impedance_gains = namedtuple('impedance_gains','kp ki K B ff')
# current_gains = namedtuple('current_gains', 'kp ki ff')
# position_gains = namedtuple('position_gains', 'kp ki kd')


class motorListener(can.Listener):
    def __init__(self, canman, motor):
        self.canman = canman
        self.bus = canman.bus
        self.motor = motor

    def on_message_received(self, msg):
            data = bytes(msg.data)
            ID = data[0]
            if ID == self.motor.ID:
                self.motor.update_state(self.canman.parse_MIT_message(data, self.motor.type))
            


class CAN_Manager(object):
    
    debug = False
    

    # Note, defining singletons in this way means that you cannot inherit
    # from this class, as apparently __init__ will be called twice
    _instance = None
    def __new__(cls):
        if not cls._instance:
            cls._instance = super(CAN_Manager, cls).__new__(cls)
            print("Initializing CAN Manager")
            os.system( 'sudo /sbin/ip link set can0 down' ) # ['sudo', '/sbin/ip', 'link', 'set', 'can0', 'down']
            os.system( 'sudo /sbin/ip link set can0 up type can bitrate 1000000' ) # ['sudo', '/sbin/ip', 'link', 'set', 'can0', 'up', 'type', 'can', 'bitrate', '1000000']
            cls._instance.bus = can.interface.Bus(channel='can0', bustype='socketcan_native')
            cls._instance.notifier = can.Notifier(bus=cls._instance.bus, listeners=[])

        return cls._instance

    def __init__(self):
        pass
        

    def add_motor(self, motor):
        self.notifier.add_listener(motorListener(self, motor))


    # Utility methods for data manipulation
    @staticmethod
    def limit_value(value, min, max):
        if value > max:
            return max
        elif value < min:
            return min
        else:
            return value

    @staticmethod
    def float_to_uint(x,x_min,x_max,num_bits):
        x = CAN_Manager.limit_value(x,x_min,x_max)
        span = x_max-x_min
        # (x - x_min)*(2^num_bits)/span
        return int((x- x_min)*( float((1<<num_bits)/span)) )

    @staticmethod
    def uint_to_float(x,x_min,x_max,num_bits):
        span = x_max-x_min
        # (x*span/(2^num_bits -1)) + x_min
        return float(x*span/((1<<num_bits)-1) + x_min)

    # CAN Sending Functionality
    def send_MIT_message(self, motor_id, data):
        DLC = len(data)
        assert (DLC <= 8), ('Data too long in message for motor ' + str(motor_id))
        
        if self.debug:
            print('ID: ' + str(hex(motor_id)) + '   Data: ' + '[{}]'.format(', '.join(hex(d) for d in data)) )
        
        message = can.Message(arbitration_id=motor_id, data=data, is_extended_id=False)
        try:
            self.bus.send(message)
            if self.debug:
                print("    Message sent on " + str(self.bus.channel_info) )
        except can.CanError:
            if self.debug:
                print("    Message NOT sent")

    def power_on(self, motor_id):
        self.send_MIT_message(motor_id, [ 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,0XFC])
        
    def power_off(self, motor_id):
        self.send_MIT_message(motor_id, [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0XFD])

    def zero(self, motor_id):
        self.send_MIT_message(motor_id, [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFE])

    def MIT_controller(self, motor_id, motor_type, position, velocity, Kp, Kd, I):
        position_uint16 = CAN_Manager.float_to_uint(position, MIT_Params[motor_type]['P_min'], 
                                                    MIT_Params[motor_type]['P_max'], 16)
        velocity_uint12 = CAN_Manager.float_to_uint(velocity, MIT_Params[motor_type]['V_min'], 
                                                    MIT_Params[motor_type]['V_max'], 12)
        Kp_uint12 = CAN_Manager.float_to_uint(Kp, MIT_Params[motor_type]['Kp_min'], 
                                                    MIT_Params[motor_type]['Kp_max'], 12)
        Kd_uint12 = CAN_Manager.float_to_uint(Kd, MIT_Params[motor_type]['Kd_min'], 
                                                    MIT_Params[motor_type]['Kd_max'], 12)
        I_uint12 = CAN_Manager.float_to_uint(I, MIT_Params[motor_type]['I_min'], 
                                                    MIT_Params[motor_type]['I_max'], 12)

        data = [
            position_uint16 >> 8,
            position_uint16 & 0x00FF,
            (velocity_uint12) >> 4,
            ((velocity_uint12&0x00F)<<4) | (Kp_uint12) >> 8,
            (Kp_uint12&0x0FF),
            (Kd_uint12) >> 4,
            ((Kd_uint12&0x00F)<<4) | (I_uint12) >> 8,
            (I_uint12&0x0FF)
        ]

        self.send_MIT_message(motor_id, data)
        
    
    def parse_MIT_message(self, data, motor_type):
        
        assert len(data) == 8 or len(data) == 6, 'Tried to parse a CAN message that was not Motor State in MIT Mode'
        temp = None
        error = None
        position_uint = data[1] <<8 | data[2]
        velocity_uint = ((data[3] << 8) | (data[4]>>4) <<4 ) >> 4
        current_uint = (data[4]&0x0F)<<8 | data[5]
        
        if len(data)  == 8:
            temp = int(data[6])
            error = int(data[7])

        position = CAN_Manager.uint_to_float(position_uint, MIT_Params[motor_type]['P_min'], 
                                            MIT_Params[motor_type]['P_max'], 16)
        velocity = CAN_Manager.uint_to_float(velocity_uint, MIT_Params[motor_type]['V_min'], 
                                            MIT_Params[motor_type]['V_max'], 12)
        current = CAN_Manager.uint_to_float(current_uint, MIT_Params[motor_type]['I_min'], 
                                            MIT_Params[motor_type]['I_max'], 12)

        if self.debug:
            print('  Position: ' + str(position))
            print('  Velocity: ' + str(velocity))
            print('  Current: ' + str(current))
            if (temp is not None) and (error is not None):
                print('  Temp: ' + str(temp))
                print('  Error: ' + str(error))

        return MIT_motor_state(position, velocity, current, temp, error)


   
class TMotorManState(Enum):
    # VOLTAGE = 1
    # CURRENT = 2
    # POSITION = 3
    IMPEDANCE = 4
    IDLE = 5


class TMotorManager():
    def __init__(self, motor_type='AK80-9', motor_ID=1, CSV_file=None):
        self.type = motor_type
        self.ID = motor_ID


        self.motor_state = motor_state(0,0,0,0,0,0)
        self.motor_state_async = motor_state(0,0,0,0,0,0)
        self.impedance_gains = impedance_gains(0,0,0,0,0)
        self.setpoint = None
        self.control_state = TMotorManState.IDLE

        # self.current_gains = current_gains(0,0,0)
        # self.position_gains = position_gains(0,0,0)

        # self.control_variables = control_variables(0,0)
        self.control_state = TMotorManState.IDLE
        
        self.canman = CAN_Manager()
        self.canman.add_motor(self)

        self.entered = False
        self.last_update_time = 0.0
        self.updated = False
        

    def __enter__(self):
        self.power_on()
        self.entered = True
        return self

    def __exit__(self, etype, value, tb):
        self.power_off()

    def update_state(self, MIT_state):
        # is time.time() good enough?
        now = time.time()
        dt = self.last_update_time - now
        self.last_update_time = now
        # print("dt: " + str(dt))
        # seems like a hacky way to get acceleration but it has to be discrete anyway right?
        acceleration = MIT_state.velocity/dt
        self.motor_state_async = motor_state(MIT_state.position, MIT_state.velocity, MIT_state.current, MIT_state.temperature, MIT_state.error, acceleration)
        self.updated = True
        messageTimer.toc()

    def update(self):
        if not self.updated:
            # could generalize later!!
            # if not self.entered:
            #     raise 
            if self.control_state == TMotorManState.IMPEDANCE:
                self.set_motor_angle_radians(self.setpoint)
            elif self.control_state == TMotorManState.IDLE:
                self.power_on()
            
        
        self.motor_state.set_state(self.motor_state_async)
        self.updated = False

    # Basic Motor Utility Commands
    def power_on(self):
        messageTimer.tic()
        self.canman.power_on(self.ID)
        self.updated = True

    def power_off(self):
        # messageTimer.tic()
        self.canman.power_off(self.ID)

    def zero_position(self):
        messageTimer.tic()
        self.canman.zero(self.ID)

    # getters for motor state
    def get_current_qaxis_amps(self):
        if (self.motor_state is None):
            raise RuntimeError("TMotorManager not updated before state is queried.")
        return self.motor_state.current

    def get_motor_angle_radians(self):
        if (self.motor_state is None):
            raise RuntimeError("TMotorManager not updated before state is queried.")
        return self.motor_state.position

    def get_motor_velocity_radians_per_second(self):
        if (self.motor_state is None):
            raise RuntimeError("TMotorManager not updated before state is queried.")
        return self.motor_state.velocity

    def get_motor_acceleration_radians_per_second_squared(self):
        if (self.motor_state is None):
            raise RuntimeError("TMotorManager not updated before state is queried.")
        return self.motor_state.acceleration

    def get_motor_torque_newton_meters(self):
        return self.get_current_qaxis_amps()*MIT_Params[self.type]["NM_PER_AMP"]

        

    # setting gains
    def set_position_gains(self, kp=200, ki=50, kd=0):
        # assert(isfinite(kp) and 0 <= kp and kp <= 500)
        # assert(isfinite(ki) and 0 <= ki and ki <= 1000)
        # assert(isfinite(kd) and 0 <= kd and kd <= 1000)
        # self.position_gains = (kp, ki, kd)
        # self.control_state=TMotorManState.POSITION
        # self.control_variables = (0.0,0.0,0.0,0.0)
        # self.set_motor_angle_radians(self.get_motor_angle_radians())
        raise NotImplemented()

    def set_current_gains(self, kp=40, ki=400, ff=128, spoof=False):
        # what does spoof do?
        # assert(isfinite(kp) and 0 <= kp and kp <= 500) # ours goes up to 500
        # assert(isfinite(ki) and 0 <= ki and ki <= 800)
        # assert(isfinite(ff) and 0 <= ff and ff <= 128)
        # self.current_gains = (kp, ki, ff)
        # self.control_state = TMotorManState.CURRENT
        # self.control_variables = (0.0,0.0,0.0,0)
        # self.set_current_qaxis_amps(0.0)
        raise NotImplemented()

    def set_impedance_gains_real_unit_KB(self, kp=40, ki=400, K=300, B=1600, ff=128):
        # assert(isfinite(kp) and 0 <= kp and kp <= 80)
        # assert(isfinite(ki) and 0 <= ki and ki <= 800)
        # assert(isfinite(ff) and 0 <= ff and ff <= 128)
        
        assert(isfinite(K) and MIT_Params[self.type]["Kp_min"] <= K and K <= MIT_Params[self.type]["Kp_max"])
        assert(isfinite(B) and MIT_Params[self.type]["Kd_min"] <= B and B <= MIT_Params[self.type]["Kd_max"])
        self.impedance_gains = impedance_gains(kp,ki,K,B,ff)
        self.control_state = TMotorManState.IMPEDANCE
        self.control_variables = (0,0,0,0)
        self.set_motor_angle_radians(self.get_motor_angle_radians())

    # controller setters
    def set_motor_angle_radians(self, pos):
        messageTimer.tic()
        if self.control_state != TMotorManState.IMPEDANCE:
            raise RuntimeError("Motor must be in position or impedance mode to accept a position setpoint")
        self.setpoint = pos
        self.canman.MIT_controller(self.ID,self.type, pos, 0.0, self.impedance_gains.K, self.impedance_gains.B, 0.0)

    def set_current_qaxis_amps(self, current_q):
        raise NotImplemented()
        # if self.control_state != TMotorManState.CURRENT:
        #     raise RuntimeError("Motor must be in current mode to accept a current command")
        # self.control_variables.setpoint = current_q
        
        # # what to do with feed forward gain? If that's what ff stands for? Units? 
        # control_signal = self.control_variables.error*self.current_gains.Kp + self.control_variables.error_integral*self.current_gains.ki
        # self.canman.MIT_controller(self.ID,self.type, 0.0, 0.0, 0.0, 0.0, control_signal)

    def print_state(self, overwrite=False):

        if overwrite:
            printstr = "\rPosition: " + str(round(self.motor_state.position,4)) + "rad | Velocity: " + str(round(self.motor_state.velocity,4)) + "rad/s | current: " + str(round(self.motor_state.current,4)) + "A"
            print(printstr,end = '')
        else:
            print(printstr = "Position: " + str(round(self.motor_state.position,4)) + "rad | Velocity: " + str(round(self.motor_state.velocity,4)) + "rad/s | current: " + str(round(self.motor_state.current,4)) + "A")

    


if __name__ == "__main__":
    messageTimer = StatProfiler.StatProfiler("messageTimer")
    
    ## Should the canman use a with block too? Almost certainly
    with TMotorManager(motor_type='AK80-9', motor_ID=3) as motor3:
        
        motor3.zero_position()
        
        motor3.update()
        
        time.sleep(1)
        motor3.set_impedance_gains_real_unit_KB(0,0,10,1,0)
        motor3.set_motor_angle_radians(3.14/2)
        loop = SoftRealtimeLoop(dt = 0.001, report=True, fade=0.0)

        for t in loop:
            motor3.update()
        del loop

    del messageTimer

            


""" 
TODO:
Test Timing
Rework controller to save current setpoints and then change those setpoints with intelligent commands
    MIT Mode: specify position and current
    Set total Current Mode (over writes MIT position control): specify current
    Set additional Current Mode (still in MIT mode): specify current, will use old command for position

Use the softrealtime fading function
Verify timing in a more robust way
Add in the fancy Greek variable notation

"""

        


        






