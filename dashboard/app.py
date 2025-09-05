from flask import Flask, render_template, request, redirect, url_for, jsonify
import redis
import docker
import time

app = Flask(__name__)

# Connect to Redis service (defined in docker-compose.yml)
redis_client = redis.StrictRedis(host='redis', port=6379, db=0)

# Connect to Docker daemon
try:
    docker_client = docker.from_env()
except Exception as e:
    print(f"Error connecting to Docker daemon: {e}")
    docker_client = None

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

    # Fetch historical data for charts
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

    return render_template('index.html', 
                           unique_ips=unique_ips, 
                           total_requests_24h=total_requests_24h,
                           containers=containers_data,
                           unique_ips_labels=unique_ips_labels,
                           unique_ips_data=unique_ips_data,
                           total_requests_labels=total_requests_labels,
                           total_requests_data=total_requests_data)

# Placeholder for WAF rule configuration (will be implemented in Phase 2)
@app.route('/configure_waf/<container_name>', methods=['GET', 'POST'])
def configure_waf(container_name):
    # In Phase 2, we will fetch/save rules for this container from Redis
    return f"Configure WAF for {container_name}"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
