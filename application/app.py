#!/usr/bin/env python3
from flask import Flask, render_template, request, jsonify
import multiprocessing
import time
import psutil
import threading
import os
import math

app = Flask(__name__)

# Flag to control CPU load generation
generate_load = False
load_thread = None

def cpu_load_thread():
    """Thread function that generates CPU load while generate_load is True"""
    while generate_load:
        # Create multiple worker processes to increase CPU load
        processes = []
        for _ in range(os.cpu_count()):
            process = multiprocessing.Process(target=cpu_intensive_task)
            process.start()
            processes.append(process)
        
        for process in processes:
            process.join()

def cpu_intensive_task():
    # A more intensive CPU task
    for _ in range(10000000):
        math.factorial(100)

@app.route('/')
def index():
    """Main page showing current resource usage"""
    cpu_percent = psutil.cpu_percent(interval=1)
    memory_percent = psutil.virtual_memory().percent
    disk_percent = psutil.disk_usage('/').percent
    
    # Get hostname and IP for display
    hostname = os.uname()[1]
    ip_address = "Local VM"  # This would be different in the cloud
    
    return render_template('index.html', 
                          cpu=cpu_percent, 
                          memory=memory_percent, 
                          disk=disk_percent,
                          hostname=hostname,
                          ip_address=ip_address,
                          is_generating_load=generate_load)

@app.route('/api/status')
def status():
    """API endpoint returning current resource usage"""
    cpu_percent = psutil.cpu_percent(interval=1)
    memory_percent = psutil.virtual_memory().percent
    disk_percent = psutil.disk_usage('/').percent
    
    return jsonify({
        "cpu": cpu_percent,
        "memory": memory_percent,
        "disk": disk_percent,
        "average": (cpu_percent + memory_percent + disk_percent) / 3,
        "load_active": generate_load
    })

@app.route('/api/load', methods=['POST'])
def toggle_load():
    """API endpoint to start/stop CPU load generation"""
    global generate_load, load_thread
    
    action = request.json.get('action', 'status')
    
    if action == 'start' and not generate_load:
        generate_load = True
        load_thread = threading.Thread(target=cpu_load_thread)
        load_thread.daemon = True
        load_thread.start()
        return jsonify({"status": "Load generation started", "active": True})
    
    elif action == 'stop' and generate_load:
        generate_load = False
        if load_thread:
            load_thread.join(timeout=1.0)
        return jsonify({"status": "Load generation stopped", "active": False})
    
    return jsonify({"status": "Current load status", "active": generate_load})

if __name__ == '__main__':
    # Create templates directory if it doesn't exist
    os.makedirs('templates', exist_ok=True)
    
    # Create the HTML template
    with open('templates/index.html', 'w') as f:
        f.write("""<!DOCTYPE html>
<html>
<head>
    <title>VM Resource Monitor</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
        }
        .card {
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 4px 8px rgba(0,0,0,0.1);
        }
        .resource-bar {
            height: 24px;
            background-color: #e0e0e0;
            border-radius: 12px;
            margin: 10px 0;
            overflow: hidden;
        }
        .resource-value {
            height: 100%;
            background-color: #4CAF50;
            text-align: center;
            line-height: 24px;
            color: white;
            transition: width 0.5s ease-in-out;
        }
        .warning {
            background-color: #FFC107;
        }
        .danger {
            background-color: #F44336;
        }
        .controls {
            display: flex;
            gap: 10px;
            margin-top: 20px;
        }
        button {
            padding: 10px 15px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-weight: bold;
        }
        .start {
            background-color: #4CAF50;
            color: white;
        }
        .stop {
            background-color: #F44336;
            color: white;
        }
        .refresh {
            background-color: #2196F3;
            color: white;
        }
    </style>
</head>
<body>
    <h1>VM Resource Monitor</h1>
    
    <div class="card">
        <h2>System Information</h2>
        <p><strong>Hostname:</strong> {{ hostname }}</p>
        <p><strong>IP Address:</strong> {{ ip_address }}</p>
    </div>
    
    <div class="card">
        <h2>Resource Usage</h2>
        
        <h3>CPU: <span id="cpu-value">{{ cpu }}%</span></h3>
        <div class="resource-bar">
            <div id="cpu-bar" class="resource-value" style="width: {{ cpu }}%;">{{ cpu }}%</div>
        </div>
        
        <h3>Memory: <span id="memory-value">{{ memory }}%</span></h3>
        <div class="resource-bar">
            <div id="memory-bar" class="resource-value" style="width: {{ memory }}%;">{{ memory }}%</div>
        </div>
        
        <h3>Disk: <span id="disk-value">{{ disk }}%</span></h3>
        <div class="resource-bar">
            <div id="disk-bar" class="resource-value" style="width: {{ disk }}%;">{{ disk }}%</div>
        </div>
        
        <div class="controls">
            <button id="load-button" class="{% if is_generating_load %}stop{% else %}start{% endif %}">
                {% if is_generating_load %}Stop Load Generation{% else %}Start Load Generation{% endif %}
            </button>
            <button id="refresh-button" class="refresh">Refresh Data</button>
        </div>
    </div>
    
    <div class="card">
        <h2>Auto-Scaling Information</h2>
        <p>This application will automatically scale to Google Cloud Platform when resource usage exceeds 75%.</p>
        <p>Current average usage: <strong><span id="avg-usage">{{ (cpu + memory + disk) / 3 }}%</span></strong></p>
    </div>

    <script>
        // Function to update the UI with new resource values
        function updateResourceUI(data) {
            document.getElementById('cpu-value').textContent = data.cpu.toFixed(1) + '%';
            document.getElementById('cpu-bar').textContent = data.cpu.toFixed(1) + '%';
            document.getElementById('cpu-bar').style.width = data.cpu + '%';
            
            document.getElementById('memory-value').textContent = data.memory.toFixed(1) + '%';
            document.getElementById('memory-bar').textContent = data.memory.toFixed(1) + '%';
            document.getElementById('memory-bar').style.width = data.memory + '%';
            
            document.getElementById('disk-value').textContent = data.disk.toFixed(1) + '%';
            document.getElementById('disk-bar').textContent = data.disk.toFixed(1) + '%';
            document.getElementById('disk-bar').style.width = data.disk + '%';
            
            document.getElementById('avg-usage').textContent = data.average.toFixed(1) + '%';
            
            // Update colors based on usage
            [
                { id: 'cpu-bar', value: data.cpu },
                { id: 'memory-bar', value: data.memory },
                { id: 'disk-bar', value: data.disk }
            ].forEach(item => {
                const element = document.getElementById(item.id);
                element.classList.remove('warning', 'danger');
                if (item.value >= 75) {
                    element.classList.add('danger');
                } else if (item.value >= 50) {
                    element.classList.add('warning');
                }
            });
            
            // Update load button text
            const loadButton = document.getElementById('load-button');
            if (data.load_active) {
                loadButton.textContent = 'Stop Load Generation';
                loadButton.classList.remove('start');
                loadButton.classList.add('stop');
            } else {
                loadButton.textContent = 'Start Load Generation';
                loadButton.classList.remove('stop');
                loadButton.classList.add('start');
            }
        }
        
        // Function to fetch status data
        function fetchStatus() {
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {
                    updateResourceUI(data);
                })
                .catch(error => {
                    console.error('Error fetching status:', error);
                });
        }
        
        // Set up refresh button
        document.getElementById('refresh-button').addEventListener('click', fetchStatus);
        
        // Set up load generation button
        document.getElementById('load-button').addEventListener('click', function() {
            const action = this.classList.contains('start') ? 'start' : 'stop';
            
            fetch('/api/load', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ action: action })
            })
            .then(response => response.json())
            .then(data => {
                fetchStatus();
            })
            .catch(error => {
                console.error('Error toggling load:', error);
            });
        });
        
        // Refresh data every 5 seconds
        setInterval(fetchStatus, 5000);
    </script>
</body>
</html>""")
    
    # Create requirements.txt
    with open('requirements.txt', 'w') as f:
        f.write("Flask==2.0.1\npsutil==5.9.0")
    
    # Start the app
    app.run(host='0.0.0.0', port=5000)
