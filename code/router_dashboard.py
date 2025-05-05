'''
repo : https://github.com/openmarmot/router-dashboard
email : andrew@openmarmot.com
notes : a simple flask dashboard for a linux router
'''

import sqlite3
import time
from datetime import datetime
from flask import Flask, render_template, jsonify
import threading
import subprocess

app = Flask(__name__)

# Initialize SQLite database
def init_db():
    conn = sqlite3.connect('router_data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS traffic 
                 (id INTEGER PRIMARY KEY, timestamp DATETIME, 
                  interface TEXT, bytes_sent INTEGER, bytes_received INTEGER)''')
    conn.commit()
    conn.close()

# Collect network traffic data from /proc/net/dev
def collect_traffic_data():
    with open('/proc/net/dev', 'r') as f:
        lines = f.readlines()[2:]  # Skip header lines
    interfaces = ['ens18', 'ens19']
    conn = sqlite3.connect('router_data.db')
    c = conn.cursor()
    timestamp = datetime.now()
    for line in lines:
        parts = line.split()
        iface = parts[0].strip(':')
        if iface in interfaces:
            bytes_received = int(parts[1])
            bytes_sent = int(parts[9])
            c.execute('INSERT INTO traffic (timestamp, interface, bytes_sent, bytes_received) VALUES (?, ?, ?, ?)',
                      (timestamp, iface, bytes_sent, bytes_received))
    conn.commit()
    conn.close()

# Parse DHCP leases from /var/lib/dhcpd/dhcpd.leases
def get_dhcp_leases():
    leases = []
    current_lease = {}
    with open('/var/lib/dhcpd/dhcpd.leases', 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('lease'):
                if current_lease:
                    leases.append(current_lease)
                current_lease = {'ip': line.split()[1]}
            elif line.startswith('ends'):
                current_lease['ends'] = ' '.join(line.split()[2:]).strip(';')
            elif line.startswith('hardware ethernet'):
                current_lease['mac'] = line.split()[2].strip(';')
            elif line.startswith('client-hostname'):
                current_lease['hostname'] = line.split()[1].strip('";')
        if current_lease:
            leases.append(current_lease)
    
    # Filter active leases
    now = datetime.now()
    active_leases = []
    for lease in leases:
        if 'ends' in lease:
            end_time = datetime.strptime(lease['ends'], '%Y/%m/%d %H:%M:%S')
            if now < end_time:
                active_leases.append({
                    'ip': lease['ip'],
                    'mac': lease.get('mac', 'N/A'),
                    'hostname': lease.get('hostname', 'N/A')
                })
    return active_leases

# Function to get CPU usage using mpstat
def get_cpu_usage():
    try:
        # Run mpstat 1 1 to get CPU stats over 1 second
        result = subprocess.run(['mpstat', '1', '1'], capture_output=True, text=True, check=True)
        lines = result.stdout.splitlines()
        if len(lines) > 3:
            # Extract the idle percentage from the last line
            idle = float(lines[3].split()[-1])
            cpu_usage = 100 - idle
            return cpu_usage
        else:
            return None
    except FileNotFoundError:
        print("Error: mpstat not found. Please install sysstat.")
        return None
    except Exception as e:
        print(f"Error getting CPU usage: {e}")
        return None

# Function to get memory usage using free
def get_memory_usage():
    try:
        # Run free -m to get memory stats in MB
        result = subprocess.run(['free', '-m'], capture_output=True, text=True, check=True)
        lines = result.stdout.splitlines()
        if len(lines) > 1:
            # Extract total and used memory from the second line
            parts = lines[1].split()
            total = int(parts[1])
            used = int(parts[2])
            memory_usage = (used / total) * 100
            return memory_usage
        else:
            return None
    except Exception as e:
        print(f"Error getting memory usage: {e}")
        return None

# Background thread function for periodic data collection
def run_periodic_task(stop_event):
    while not stop_event.is_set():
        try:
            collect_traffic_data()
            time.sleep(5)  # Collect data every 5 seconds
        except Exception as e:
            print(f"Error in background task: {e}")

# Dashboard route
@app.route('/')
def dashboard():
    return render_template('dashboard.html')

# Traffic data endpoint
@app.route('/traffic_data')
def traffic_data():
    conn = sqlite3.connect('router_data.db')
    c = conn.cursor()
    c.execute('SELECT timestamp, interface, bytes_sent, bytes_received FROM traffic ORDER BY timestamp DESC LIMIT 60')
    rows = c.fetchall()
    conn.close()
    data = {'ens18': {'times': [], 'sent': [], 'received': []}, 
            'ens19': {'times': [], 'sent': [], 'received': []}}
    for row in reversed(rows):
        ts, iface, sent, recv = row
        data[iface]['times'].append(ts.strftime('%H:%M:%S'))
        data[iface]['sent'].append(sent / 1024)  # Convert to KB
        data[iface]['received'].append(recv / 1024)
    return jsonify(data)

# DHCP leases endpoint
@app.route('/dhcp_leases')
def dhcp_leases():
    return jsonify(get_dhcp_leases())

# System health endpoint
@app.route('/system_health')
def system_health():
    cpu_usage = get_cpu_usage()
    memory_usage = get_memory_usage()
    return jsonify({
        'cpu_percent': cpu_usage if cpu_usage is not None else 0,
        'memory_percent': memory_usage if memory_usage is not None else 0
    })

if __name__ == '__main__':
    init_db()
    collect_traffic_data()  # Initial data collection

    # Create a stop event to control the thread
    stop_event = threading.Event()
    
    # Start the background thread for periodic data collection
    data_thread = threading.Thread(target=run_periodic_task, args=(stop_event,))
    data_thread.start()
    
    try:
        # Run the Flask app
        app.run(host='0.0.0.0', port=5000, debug=False)
    finally:
        # Stop the thread gracefully on app shutdown
        stop_event.set()
        data_thread.join()
        print("Application stopped.")