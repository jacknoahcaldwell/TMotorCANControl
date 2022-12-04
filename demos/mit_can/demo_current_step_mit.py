from NeuroLocoMiddleware.SoftRealtimeLoop import SoftRealtimeLoop
import time
from TMotorCANControl.TMotorManager_mit_can import TMotorManager_mit_can

# CHANGE THESE TO MATCH YOUR DEVICE!
Type = 'AK80-9'
ID = 1

def current_step(dev):
    dev.zero_position()
    time.sleep(1.5) # wait for the motor to zero (~1 second)
    dev.set_current_gains()
    
    print("Starting current step demo. Press ctrl+C to quit.")

    loop = SoftRealtimeLoop(dt = 0.01, report=True, fade=0)
    for t in loop:
        dev.update()
        if t < 1.0:
            dev.i = 0.0
        else:
            dev.i = 0.5

    del loop

if __name__ == '__main__':
    with TMotorManager_mit_can(motor_type=Type, motor_ID=ID) as dev:
        current_step(dev)