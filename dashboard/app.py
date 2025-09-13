from flask import Flask, render_template, request, redirect, url_for, jsonify
import redis
import docker
import time
import psutil
import threading
import math
import os

app = Flask(__name__)

# Connect to Redis service (defined in docker-compose.yml)
redis_client = redis.StrictRedis(host='redis', port=6379, db=0)

# Connect to Docker daemon
try:
    docker_client = docker.from_env()
except Exception as e:
    print(f"Error connecting to Docker daemon: {e}")
    docker_client = None

# Configure psutil to read from host's /proc and /sys if mounted
if os.path.exists('/host/proc') and os.path.exists('/host/sys'):
    psutil.PROCFS_PATH = '/host/proc'
    psutil.SYSFS_PATH = '/host/sys'
    print("psutil configured to read host system metrics.")
else:
    print("psutil reading container system metrics (host /proc and /sys not found at /host).")

# --- System Metrics Collection ---
SYSTEM_METRICS_HISTORY_LENGTH = 1440 # 24 hours * 60 minutes
CONTAINER_METRICS_HISTORY_LENGTH = 60 # 1 hour * 60 seconds

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
        disk = psutil.disk_usage('/host')
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
        # Schedule next collection in 10 seconds
        threading.Timer(10, collect_system_metrics).start()

def collect_container_metrics():
    if not docker_client:
        threading.Timer(10, collect_container_metrics).start()
        return

    try:
        timestamp = math.floor(time.time())
        for container in docker_client.containers.list():
            stats = container.stats(stream=False) # Get a single snapshot
            
            # CPU Usage Calculation (Docker SDK specific)
            cpu_percent = 0.0
            if 'cpu_stats' in stats and 'precpu_stats' in stats:
                cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - stats['precpu_stats']['cpu_usage']['total_usage']
                system_delta = stats['cpu_stats']['system_cpu_usage'] - stats['precpu_stats']['system_cpu_usage']
                number_cpus = stats['cpu_stats']['system_cpu_usage'] # This is actually total_cpus, not system_cpu_usage
                if number_cpus > 0 and system_delta > 0:
                    cpu_percent = (cpu_delta / system_delta) * number_cpus * 100.0
            
            # Memory Usage Calculation
            mem_usage = 0
            mem_limit = 0
            mem_percent = 0.0
            if 'memory_stats' in stats:
                mem_usage = stats['memory_stats'].get('usage', 0)
                mem_limit = stats['memory_stats'].get('limit', 0)
                if mem_limit > 0:
                    mem_percent = (mem_usage / mem_limit) * 100.0

            redis_client.lpush(f'container:{container.id}:cpu_history', f"{timestamp}:{cpu_percent:.2f}")
            redis_client.ltrim(f'container:{container.id}:cpu_history', 0, CONTAINER_METRICS_HISTORY_LENGTH - 1)
            
            redis_client.lpush(f'container:{container.id}:memory_history', f"{timestamp}:{mem_percent:.2f}")
            redis_client.ltrim(f'container:{container.id}:memory_history', 0, CONTAINER_METRICS_HISTORY_LENGTH - 1)

    except Exception as e:
        print(f"Error collecting container metrics: {e}")
    finally:
        # Schedule next collection in 10 seconds
        threading.Timer(10, collect_container_metrics).start()

# Start the system metrics collection thread
# We need to ensure this runs only once when the app starts
# For development with debug=True, Flask reloads the app, so this might run twice.
# In production (without debug=True), it will run once.
if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    threading.Timer(1, collect_system_metrics).start() # Start after 1 second
    threading.Timer(1, collect_container_metrics).start() # Start after 1 second


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
                # Fetch latest CPU and Memory usage from Redis
                latest_cpu_raw = redis_client.lindex(f'container:{container.id}:cpu_history', 0)
                latest_memory_raw = redis_client.lindex(f'container:{container.id}:memory_history', 0)

                latest_cpu_usage = "N/A"
                if latest_cpu_raw:
                    try:
                        latest_cpu_usage = float(latest_cpu_raw.decode('utf-8').split(':')[1])
                        latest_cpu_usage = f"{latest_cpu_usage:.2f}%"
                    except (ValueError, IndexError):
                        pass

                latest_memory_usage = "N/A"
                if latest_memory_raw:
                    try:
                        latest_memory_usage = float(latest_memory_raw.decode('utf-8').split(':')[1])
                        latest_memory_usage = f"{latest_memory_usage:.2f}%"
                    except (ValueError, IndexError):
                        pass

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
                    'ports': ", ".join(ports) if ports else '-',
                    'cpu_usage': latest_cpu_usage,
                    'memory_usage': latest_memory_usage
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

    # Fetch WAF rules for display
    waf_mode = redis_client.get('waf:mode')
    if waf_mode:
        waf_mode = waf_mode.decode('utf-8')
    else:
        waf_mode = 'block' # Default mode

    ip_blacklist = [ip.decode('utf-8') for ip in redis_client.smembers('waf:ip_blacklist')]
    sql_patterns = [pattern.decode('utf-8') for pattern in redis_client.smembers('waf:sql_patterns')]
    xss_patterns = [pattern.decode('utf-8') for pattern in redis_client.smembers('waf:xss_patterns')]

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
                           network_recv_data=network_recv_data,
                           waf_mode=waf_mode,
                           ip_blacklist=ip_blacklist,
                           sql_patterns=sql_patterns,
                           xss_patterns=xss_patterns)

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

@app.route('/api/containers/<container_id>/metrics')
def get_container_metrics(container_id):
    cpu_history_raw = redis_client.lrange(f'container:{container_id}:cpu_history', 0, -1)
    memory_history_raw = redis_client.lrange(f'container:{container_id}:memory_history', 0, -1)

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

    return jsonify({
        'cpu_labels': cpu_labels,
        'cpu_data': cpu_data,
        'memory_labels': memory_labels,
        'memory_data': memory_data
    })

@app.route('/api/containers/create', methods=['POST'])
def create_container():
    if not docker_client:
        return jsonify({'success': False, 'message': 'Docker daemon not connected.'}), 500

    data = request.get_json()
    image_name = data.get('image_name')
    container_name = data.get('container_name')
    port_mappings_str = data.get('port_mappings', '') # e.g., "80:8080,443:8443"

    if not image_name:
        return jsonify({'success': False, 'message': 'Image name is required.'}), 400

    ports = {}
    if port_mappings_str:
        for mapping in port_mappings_str.split(','):
            if ':' in mapping:
                host_port, container_port = mapping.split(':')
                ports[f'{container_port}/tcp'] = int(host_port)
            else:
                # If only container port is provided, expose it without host mapping
                ports[f'{mapping}/tcp'] = None

    try:
        container = docker_client.containers.run(
            image_name,
            name=container_name,
            ports=ports,
            detach=True # Run in background
        )
        return jsonify({'success': True, 'message': f'Container {container.name} created successfully.'})
    except docker.errors.ImageNotFound:
        return jsonify({'success': False, 'message': f'Image "{image_name}" not found.'}), 404
    except docker.errors.APIError as e:
        return jsonify({'success': False, 'message': f'Docker API error: {e}'}), 500
    except Exception as e:
        return jsonify({'success': False, 'message': f'An unexpected error occurred: {e}'}), 500

@app.route('/api/containers/<container_id>/start', methods=['POST'])
def start_container(container_id):
    if not docker_client:
        return jsonify({'success': False, 'message': 'Docker daemon not connected.'}), 500
    try:
        container = docker_client.containers.get(container_id)
        container.start()
        return jsonify({'success': True, 'message': f'Container {container.name} started successfully.'})
    except docker.errors.NotFound:
        return jsonify({'success': False, 'message': f'Container {container_id} not found.'}), 404
    except docker.errors.APIError as e:
        return jsonify({'success': False, 'message': f'Docker API error: {e}'}), 500
    except Exception as e:
        return jsonify({'success': False, 'message': f'An unexpected error occurred: {e}'}), 500

@app.route('/api/containers/<container_id>/stop', methods=['POST'])
def stop_container(container_id):
    if not docker_client:
        return jsonify({'success': False, 'message': 'Docker daemon not connected.'}), 500
    try:
        container = docker_client.containers.get(container_id)
        container.stop()
        return jsonify({'success': True, 'message': f'Container {container.name} stopped successfully.'})
    except docker.errors.NotFound:
        return jsonify({'success': False, 'message': f'Container {container_id} not found.'}), 404
    except docker.errors.APIError as e:
        return jsonify({'success': False, 'message': f'Docker API error: {e}'}), 500
    except Exception as e:
        return jsonify({'success': False, 'message': f'An unexpected error occurred: {e}'}), 500

@app.route('/api/containers/<container_id>/restart', methods=['POST'])
def restart_container(container_id):
    if not docker_client:
        return jsonify({'success': False, 'message': 'Docker daemon not connected.'}), 500
    try:
        container = docker_client.containers.get(container_id)
        container.restart()
        return jsonify({'success': True, 'message': f'Container {container.name} restarted successfully.'})
    except docker.errors.NotFound:
        return jsonify({'success': False, 'message': f'Container {container_id} not found.'}), 404
    except docker.errors.APIError as e:
        return jsonify({'success': False, 'message': f'Docker API error: {e}'}), 500
    except Exception as e:
        return jsonify({'success': False, 'message': f'An unexpected error occurred: {e}'}), 500

@app.route('/api/containers/<container_id>/remove', methods=['DELETE'])
def remove_container(container_id):
    if not docker_client:
        return jsonify({'success': False, 'message': 'Docker daemon not connected.'}), 500
    try:
        container = docker_client.containers.get(container_id)
        container.remove()
        return jsonify({'success': True, 'message': f'Container {container.name} removed successfully.'})
    except docker.errors.NotFound:
        return jsonify({'success': False, 'message': f'Container {container_id} not found.'}), 404
    except docker.errors.APIError as e:
        return jsonify({'success': False, 'message': f'Docker API error: {e}'}), 500
    except Exception as e:
        return jsonify({'success': False, 'message': f'An unexpected error occurred: {e}'}), 500

@app.route('/waf_rules', methods=['GET', 'POST'])
def waf_rules():
    if request.method == 'POST':
        # Update WAF Mode
        new_waf_mode = request.form.get('waf_mode')
        if new_waf_mode in ['block', 'monitor']:
            redis_client.set('waf:mode', new_waf_mode)

        # Update IP Blacklist
        new_ip_blacklist_str = request.form.get('ip_blacklist', '')
        new_ip_blacklist = [ip.strip() for ip in new_ip_blacklist_str.split(',') if ip.strip()]
        redis_client.delete('waf:ip_blacklist')
        if new_ip_blacklist:
            redis_client.sadd('waf:ip_blacklist', *new_ip_blacklist)

        # Update SQL Patterns
        new_sql_patterns_str = request.form.get('sql_patterns', '')
        new_sql_patterns = [pattern.strip() for pattern in new_sql_patterns_str.split(',') if pattern.strip()]
        redis_client.delete('waf:sql_patterns')
        if new_sql_patterns:
            redis_client.sadd('waf:sql_patterns', *new_sql_patterns)

        # Update XSS Patterns
        new_xss_patterns_str = request.form.get('xss_patterns', '')
        new_xss_patterns = [pattern.strip() for pattern in new_xss_patterns_str.split(',') if pattern.strip()]
        redis_client.delete('waf:xss_patterns')
        if new_xss_patterns:
            redis_client.sadd('waf:xss_patterns', *new_xss_patterns)

        return redirect(url_for('waf_rules'))

    # GET request: Fetch current WAF rules
    waf_mode = redis_client.get('waf:mode')
    if waf_mode:
        waf_mode = waf_mode.decode('utf-8')
    else:
        waf_mode = 'block' # Default mode

    ip_blacklist = [ip.decode('utf-8') for ip in redis_client.smembers('waf:ip_blacklist')]
    sql_patterns = [pattern.decode('utf-8') for pattern in redis_client.smembers('waf:sql_patterns')]
    xss_patterns = [pattern.decode('utf-8') for pattern in redis_client.smembers('waf:xss_patterns')]

    return render_template('waf_rules.html',
                           waf_mode=waf_mode,
                           ip_blacklist=ip_blacklist,
                           sql_patterns=sql_patterns,
                           xss_patterns=xss_patterns)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)