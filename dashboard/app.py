from flask import Flask, render_template, request, redirect, url_for, jsonify
import redis
import docker
import time
import psutil
import threading
import math

app = Flask(__name__)

# Connect to Redis service (defined in docker-compose.yml)
redis_client = redis.StrictRedis(host='redis', port=6379, db=0)

# Connect to Docker daemon
try:
    docker_client = docker.from_env()
except Exception as e:
    print(f"Error connecting to Docker daemon: {e}")
    docker_client = None

# --- System Metrics Collection ---
SYSTEM_METRICS_HISTORY_LENGTH = 1440 # 24 hours * 60 minutes

def collect_system_metrics():
    try:
        timestamp = math.floor(time.time())

        # CPU Usage
        cpu_percent = psutil.cpu_percent(interval=1) # Blocking call for 1 second
        redis_client.lpush('cpu_history', f"{timestamp}:{cpu_percent}")
        redis_client.ltrim('cpu_history', 0, SYSTEM_METRICS_HISTORY_LENGTH - 1)

        # Memory Usage
        mem = psutil.virtual_memory()
        mem_percent = mem.percent
        redis_client.lpush('memory_history', f"{timestamp}:{mem_percent}")
        redis_client.ltrim('memory_history', 0, SYSTEM_METRICS_HISTORY_LENGTH - 1)

        # Disk Usage (for root partition)
        disk = psutil.disk_usage('/')
        disk_percent = disk.percent
        redis_client.lpush('disk_history', f"{timestamp}:{disk_percent}")
        redis_client.ltrim('disk_history', 0, SYSTEM_METRICS_HISTORY_LENGTH - 1)

        # Network I/O (total bytes sent/received)
        net_io = psutil.net_io_counters()
        bytes_sent = net_io.bytes_sent
        bytes_recv = net_io.bytes_recv
        redis_client.lpush('network_history', f"{timestamp}:{bytes_sent}:{bytes_recv}")
        redis_client.ltrim('network_history', 0, SYSTEM_METRICS_HISTORY_LENGTH - 1)

    except Exception as e:
        print(f"Error collecting system metrics: {e}")
    finally:
        # Schedule next collection in 60 seconds
        threading.Timer(10, collect_system_metrics).start()

# Start the system metrics collection thread
# We need to ensure this runs only once when the app starts
# For development with debug=True, Flask reloads the app, so this might run twice.
# In production (without debug=True), it will run once.
if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    threading.Timer(1, collect_system_metrics).start() # Start after 1 second


@app.route('/')
def index():
    unique_ips = redis_client.scard('unique_ips')
    total_requests_24h = redis_client.get('total_requests_24h')
    if total_requests_24h:
        total_requests_24h = int(total_requests_24h.decode('utf-8'))
    else:
        total_requests_24h = 0

    containers_data = []
    if docker_client:
        try:
            for container in docker_client.containers.list():
                ports = []
                for p_binding in container.ports.values():
                    if p_binding:
                        for binding in p_binding:
                            ports.append(f"{binding['HostIp']}:{binding['HostPort']}->{binding['PrivatePort']}/{binding['Type']}")
                containers_data.append({
                    'id': container.short_id,
                    'name': container.name,
                    'status': container.status,
                    'image': container.image.tags[0] if container.image.tags else '<none>',
                    'ports': ", ".join(ports) if ports else '-'
                })
        except Exception as e:
            print(f"Error listing Docker containers: {e}")

    # Fetch historical data for WAF charts
    unique_ips_history_raw = redis_client.lrange('unique_ips_history', 0, -1)
    total_requests_24h_history_raw = redis_client.lrange('total_requests_24h_history', 0, -1)

    unique_ips_labels = []
    unique_ips_data = []
    for entry in reversed(unique_ips_history_raw): # Display in chronological order
        timestamp, count = entry.decode('utf-8').split(':')
        unique_ips_labels.append(time.strftime('%H:%M', time.localtime(int(timestamp))))
        unique_ips_data.append(int(count))

    total_requests_labels = []
    total_requests_data = []
    for entry in reversed(total_requests_24h_history_raw): # Display in chronological order
        timestamp, count = entry.decode('utf-8').split(':')
        total_requests_labels.append(time.strftime('%H:%M', time.localtime(int(timestamp))))
        total_requests_data.append(int(count))

    # Fetch historical data for System Metrics charts
    cpu_history_raw = redis_client.lrange('cpu_history', 0, -1)
    memory_history_raw = redis_client.lrange('memory_history', 0, -1)
    disk_history_raw = redis_client.lrange('disk_history', 0, -1)
    network_history_raw = redis_client.lrange('network_history', 0, -1)

    cpu_labels = []
    cpu_data = []
    for entry in reversed(cpu_history_raw):
        timestamp, percent = entry.decode('utf-8').split(':')
        cpu_labels.append(time.strftime('%H:%M', time.localtime(int(timestamp))))
        cpu_data.append(float(percent))

    memory_labels = []
    memory_data = []
    for entry in reversed(memory_history_raw):
        timestamp, percent = entry.decode('utf-8').split(':')
        memory_labels.append(time.strftime('%H:%M', time.localtime(int(timestamp))))
        memory_data.append(float(percent))

    disk_labels = []
    disk_data = []
    for entry in reversed(disk_history_raw):
        timestamp, percent = entry.decode('utf-8').split(':')
        disk_labels.append(time.strftime('%H:%M', time.localtime(int(timestamp))))
        disk_data.append(float(percent))

    network_labels = []
    network_sent_data = []
    network_recv_data = []
    for entry in reversed(network_history_raw):
        timestamp, sent, recv = entry.decode('utf-8').split(':')
        network_labels.append(time.strftime('%H:%M', time.localtime(int(timestamp))))
        network_sent_data.append(int(sent))
        network_recv_data.append(int(recv))

    return render_template('index.html', 
                           unique_ips=unique_ips, 
                           total_requests_24h=total_requests_24h,
                           containers=containers_data,
                           unique_ips_labels=unique_ips_labels,
                           unique_ips_data=unique_ips_data,
                           total_requests_labels=total_requests_labels,
                           total_requests_data=total_requests_data,
                           cpu_labels=cpu_labels,
                           cpu_data=cpu_data,
                           memory_labels=memory_labels,
                           memory_data=memory_data,
                           disk_labels=disk_labels,
                           disk_data=disk_data,
                           network_labels=network_labels,
                           network_sent_data=network_sent_data,
                           network_recv_data=network_recv_data)

@app.route('/api/metrics')
def get_metrics():
    cpu_history_raw = redis_client.lrange('cpu_history', 0, -1)
    memory_history_raw = redis_client.lrange('memory_history', 0, -1)
    disk_history_raw = redis_client.lrange('disk_history', 0, -1)
    network_history_raw = redis_client.lrange('network_history', 0, -1)

    cpu_labels = []
    cpu_data = []
    for entry in reversed(cpu_history_raw):
        timestamp, percent = entry.decode('utf-8').split(':')
        cpu_labels.append(time.strftime('%H:%M', time.localtime(int(timestamp))))
        cpu_data.append(float(percent))

    memory_labels = []
    memory_data = []
    for entry in reversed(memory_history_raw):
        timestamp, percent = entry.decode('utf-8').split(':')
        memory_labels.append(time.strftime('%H:%M', time.localtime(int(timestamp))))
        memory_data.append(float(percent))

    disk_labels = []
    disk_data = []
    for entry in reversed(disk_history_raw):
        timestamp, percent = entry.decode('utf-8').split(':')
        disk_labels.append(time.strftime('%H:%M', time.localtime(int(timestamp))))
        disk_data.append(float(percent))

    network_labels = []
    network_sent_data = []
    network_recv_data = []
    for entry in reversed(network_history_raw):
        timestamp, sent, recv = entry.decode('utf-8').split(':')
        network_labels.append(time.strftime('%H:%M', time.localtime(int(timestamp))))
        network_sent_data.append(int(sent))
        network_recv_data.append(int(recv))

    return jsonify({
        'cpu_labels': cpu_labels,
        'cpu_data': cpu_data,
        'memory_labels': memory_labels,
        'memory_data': memory_data,
        'disk_labels': disk_labels,
        'disk_data': disk_data,
        'network_labels': network_labels,
        'network_sent_data': network_sent_data,
        'network_recv_data': network_recv_data
    })

# Placeholder for WAF rule configuration (will be implemented in Phase 2)
@app.route('/configure_waf/<container_name>', methods=['GET', 'POST'])
def configure_waf(container_name):
    # In Phase 2, we will fetch/save rules for this container from Redis
    return f"Configure WAF for {container_name}"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)