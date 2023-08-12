# ========== IMPORTS ========== #
import machine, json, utime, time
import uasyncio as asyncio
from machine import Pin, ADC
from phew import server, connect_to_wifi, is_connected_to_wifi, access_point
from umqtt.simple import MQTTClient
# ============================= #





# ========== NETWORK ========== #
# Create local/ad-hoc network for setup
def connect_network():  
    # If wifi has not been configured or fails to connect, create the AP
    if wifi_settings['status'] == 0:
        access_point("HopperHawk",  password=None)
    
    # If wifi has been configured, attempt to connect (fallback on failure)
    elif wifi_settings['status'] == 1:
        ip = connect_to_wifi(wifi_settings['ssid'], wifi_settings['password'])
        if is_connected_to_wifi():
            pass
        else:
            access_point("HopperHawk",  password=None)
# ============================= #





# ========== API ========== #
# Return connection status
@server.route('/alive', methods=['GET'])
def api_alive(request):
    return str("1"), 200

# Get the current pellet level
@server.route('/pelletlevel', methods=['GET'])
def api_pelletlevel(request):
    return str(current_level), 200

# Reboot or reset the system
@server.route('/sys/<action>', methods=['POST'])
def api_syscontrol(request, action):
    if action == 'reboot':
        machine.reset()
    elif action == 'reset':
        global wifi_settings, mqtt_settings, hopper_settings
        wifi_settings = {
            'status': 0,
            'ssid': '',
            'password': ''
        }

        mqtt_settings = {
            'status': 0,
            'broker_ip': '',
            'broker_port': 1883,
            'user': '',
            'password': ''
        }

        hopper_settings = {
            'frequency': 300,
            'pellet_type': '',
            'full_measurement': 10,
            'empty_measurement': 75
        }
        
        # Save and reboot
        save_configuration('wifi')
        save_configuration('mqtt')
        save_configuration('hopper')
        machine.reset()


# Calibration
@server.route('/calibrate/<level>', methods=['GET', 'POST'])
def api_calibration(request, level):
    global config, hopper_settings
    if request.method == "GET":
        if level == "empty":
            return str(hopper_settings['empty_measurement'])
        if level == "full":
            return str(hopper_settings['full_measurement'])
    if request.method == "POST":
        if level == "empty":
            # Update the config with the new measurement
            hopper_settings['empty_measurement'] = take_measurement()
            for item in config["hopper"]:
                item['empty_measurement'] = hopper_settings['empty_measurement']
            
            # Save the config
            with open('config.json', 'w') as f:
                json.dump(config,f)
            
            # Return the measurement
            return str(hopper_settings['empty_measurement'])
        if level == "full":
            # Update the config with the new measurement
            hopper_settings['full_measurement'] = take_measurement()
            for item in config["hopper"]:
                item['full_measurement'] = hopper_settings['full_measurement']
         
            # Save the config
            with open('config.json', 'w') as f:
                json.dump(config,f)

            # Return the measurement
            return str(hopper_settings['full_measurement'])

# Configuration management
@server.route('/configure/<setting>', methods=['GET','POST'])
def api_sysconfig(request,setting):
    global wifi_settings, hopper_settings, mqtt_settings
    if request.method == 'GET':
        if setting == 'wifi':
            return json.dumps(wifi_settings), 200
        if setting == 'mqtt':
            return json.dumps(mqtt_settings), 200
        if setting == 'hopper':
            return json.dumps(hopper_settings), 200

    if request.method == 'POST':
        if setting == 'wifi':
            wifi_settings['status'] = request.data['status']
            wifi_settings['ssid'] = request.data['ssid']
            wifi_settings['password'] = request.data['password']
            save_configuration('wifi')
            
            return 'saved_wifi_settings'
        if setting == 'mqtt':
            mqtt_settings['status'] = request.data['status']
            mqtt_settings['user'] = request.data['user']
            mqtt_settings['password'] = request.data['password']
            mqtt_settings['broker_ip'] = request.data['broker_ip']
            mqtt_settings['broker_port'] = request.data['broker_port']
            save_configuration('mqtt')
            return 'saved_mqtt_settings'

        if setting == 'hopper':
            hopper_settings['frequency'] = request.data['frequency']
            save_configuration('hopper')
            return 'saved_hopper_settings'

# Catchall route for webserver
@server.catchall()
def my_catchall(request):
    return "No matching route", 404
# ========================= #





# ========== SENSOR ========== #
# Configure Hardware
scan_trigger = Pin(3, Pin.OUT)
scan_echo = Pin(2, Pin.IN)
battery = ADC(Pin(28, mode=Pin.IN))

# Battery references
max_battery_voltage = 4.5
min_battery_voltage = 1.8

# Take a measurement and return in cm
def take_measurement():
    # Trigger
    scan_trigger.low()
    utime.sleep_us(2)
    scan_trigger.high()
    utime.sleep_us(5)
    scan_trigger.low()

    # Wait for reading from receiver
    while scan_echo.value() == 0:
        signal_off = utime.ticks_us()
    while scan_echo.value() == 1:
        signal_on = utime.ticks_us()

    # Calculate distance in cm
    timepassed = (signal_on - signal_off)
    level = ((timepassed * 0.0343) / 2)
    return(level)


# Calculate remaining pellets
def calc_remaining():
    level = take_measurement()
    try:
        p_level = ((level-hopper_settings['empty_measurement'])*100)/(hopper_settings['full_measurement']-hopper_settings['empty_measurement'])
    except ZeroDivisionError:
        p_level = 0
    return(round(p_level))


# Check the battery level
def check_battery():
    # Get current voltage and estimate life remaining
    voltage = (battery.read_u16() * (3.3/65535))*2
    battery_life = ((voltage - min_battery_voltage) / (max_battery_voltage - min_battery_voltage)) * 100;

    # Report the estimated battery life
    if battery_life > 100:
        return 100
    elif battery_life < 0:
        return 0
    else:
        return battery_life
    
    # Primary function to poll sensor and report data via MQTT
def sensor_routine():
    # Main loop to continuously poll/report
    while True:
        if not calibration_mode:
            # Get the current hopper level (in cm), save globally for access via webserver
            global current_level
            current_level = calc_remaining()

            # Clean the data to make sure it doesn't go out of bounds
            if current_level < 0:
                current_level = 0
            if current_level > 100:
                current_level = 100

            # If MQTT is enabled, publish the data
            if mqtt_settings['status'] == 1:
                mqtt_publish(str(current_level),hopper_settings['pellet_type'], str(check_battery()))

            # Sleep for user-defined amount of time before polling again
            await asyncio.sleep(hopper_settings['frequency'])
# ============================ #





# ========== MQTT ========== #
# Push data to MQTT broker
def mqtt_publish(l,t,b):
    # Configure MQTT client
    client = MQTTClient('hopperhawk',mqtt_settings['broker_ip'], mqtt_settings['broker_port'], mqtt_settings['user'],mqtt_settings['password'],keepalive=60)
    
    # Try to connect and publish
    try:
        # Attempt the connection
        client.connect()

        # Publish the data
        client.publish('hopperhawk/pellets/level', msg=l)
        client.publish('hopperhawk/pellets/type', msg=t)
        client.publish('hopperhawk/sensor/battery', msg=b)

        # Wait, and disconnect
        time.sleep(.5)
        client.disconnect()

    # If it doesn't work...oh well :)
    except:
        pass
# ========================== #




# ========== ASYNC ========== #
# Main kickoff for webserver and sensor routine   
async def main():   
    # Run the sensor
    asyncio.create_task(sensor_routine())
    
    # Start the web server
    server.run()    
# =========================== #





# ========== MAIN ========== #
# Load Configuration
config = json.load(open("config.json","r"))

# Update configuration
def save_configuration(c):
    global config
    if c == 'wifi':
        for item in config["wifi"]:
            item['status'] = wifi_settings['status']
            item['ssid'] = wifi_settings['ssid']
            item['password'] = wifi_settings['password']
    elif c == 'mqtt':
        for item in config["mqtt"]:
            item['status'] = mqtt_settings['status']
            item['user'] = mqtt_settings['user']
            item['password'] = mqtt_settings['password']
            item['broker_ip'] = mqtt_settings['broker_ip']
            item['broker_port'] = mqtt_settings['broker_port']
    elif c == 'hopper':
        for item in config["hopper"]:
            item['poll_frequency'] = hopper_settings['frequency']
            item['pellet_type'] = hopper_settings['pellet_type']
            item['full_measurement'] = hopper_settings['full_measurement']
            item['empty_measurement'] = hopper_settings['empty_measurement']

    # Save the config
    with open('config.json', 'w') as f:
        json.dump(config,f)



# Working variables
current_level = 0
calibration_mode = False

wifi_settings = {
    'status': config['wifi'][0]['status'],
    'ssid': config['wifi'][0]['ssid'],
    'password': config['wifi'][0]['password']
}

mqtt_settings = {
    'status': config['mqtt'][0]['status'],
    'broker_ip': config['mqtt'][0]['ip'],
    'broker_port': config['mqtt'][0]['port'],
    'user': config['mqtt'][0]['user'],
    'password': config['mqtt'][0]['password']
}

hopper_settings = {
    'frequency': config['hopper'][0]['poll_frequency'],
    'pellet_type': config['hopper'][0]['current_pellets'],
    'full_measurement': config['hopper'][0]['full_measurement'],
    'empty_measurement': config['hopper'][0]['empty_measurement']
}

# Connect to network
connect_network()

# Start
asyncio.run(main())
# ========================== #