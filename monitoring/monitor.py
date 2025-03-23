#!/usr/bin/env python3
import psutil
import time
import subprocess
import logging
import os
import json
import requests
import google.auth.transport.requests
from google.cloud import compute_v1
from google.oauth2 import service_account
import socket
from datetime import datetime
import pickle

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename=os.path.expanduser('~/vm-autoscaler/autoscaler.log'),
    filemode='a'
)

# Configuration
THRESHOLD = 75  # 75% resource usage threshold
SCALE_DOWN_THRESHOLD = 30  # 30% resource usage threshold for scaling down
CHECK_INTERVAL = 30  # Check every 30 seconds
GCP_PROJECT_ID = "vcc-assignment-3-454613"
GCP_ZONE = "us-central1-a"
GCP_MACHINE_TYPE = "e2-standard-2"
GCP_CREDENTIALS_FILE = "/home/hari2/vm-autoscaler/vcc-assignment-3-454613-712c2b41187f.json"
APPLICATION_PATH = "/home/hari2/application"
INSTANCE_TRACKING_FILE = os.path.expanduser("~/vm-autoscaler/instances.pkl")

# Instance tracking
created_instances = []

# Load previously created instances if file exists
def load_instances():
    global created_instances
    try:
        if os.path.exists(INSTANCE_TRACKING_FILE):
            with open(INSTANCE_TRACKING_FILE, 'rb') as f:
                created_instances = pickle.load(f)
                logging.info(f"Loaded {len(created_instances)} previously created instances")
    except Exception as e:
        logging.error(f"Failed to load instances: {e}")
        created_instances = []

# Save created instances
def save_instances():
    try:
        os.makedirs(os.path.dirname(INSTANCE_TRACKING_FILE), exist_ok=True)
        with open(INSTANCE_TRACKING_FILE, 'wb') as f:
            pickle.dump(created_instances, f)
    except Exception as e:
        logging.error(f"Failed to save instances: {e}")

# Initialize Google Cloud client
def init_gcp_client():
    try:
        logging.info(f"Initializing GCP client with credentials file: {GCP_CREDENTIALS_FILE}")
        credentials = service_account.Credentials.from_service_account_file(
            GCP_CREDENTIALS_FILE, 
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        client = compute_v1.InstancesClient(credentials=credentials)
        logging.info(f"GCP client initialized successfully for project: {GCP_PROJECT_ID}")
        return client
    except Exception as e:
        logging.error(f"Failed to initialize GCP client: {e}")
        return None

# Get current VM resource usage
def get_resource_usage():
    cpu_percent = psutil.cpu_percent(interval=1)
    memory_percent = psutil.virtual_memory().percent
    disk_percent = psutil.disk_usage('/').percent
    
    # Average of CPU, memory, and disk
    average_usage = (cpu_percent + memory_percent + disk_percent) / 3
    
    logging.info(f"Resource Usage - CPU: {cpu_percent}%, Memory: {memory_percent}%, "
                f"Disk: {disk_percent}%, Average: {average_usage}%")
    
    return {
        "cpu": cpu_percent,
        "memory": memory_percent,
        "disk": disk_percent,
        "average": average_usage,
        "timestamp": datetime.now().isoformat()
    }

# Get resource usage from cloud instance
def get_cloud_instance_usage(instance_name, instance_ip):
    try:
        # Try to get resource usage via HTTP request to the Flask app
        response = requests.get(f"http://{instance_ip}:5000/api/status", timeout=10)
        if response.status_code == 200:
            data = response.json()
            logging.info(f"Cloud instance {instance_name} usage: {data['average']}%")
            return data['average']
        else:
            logging.warning(f"Failed to get resource usage from {instance_name}: HTTP {response.status_code}")
            return None
    except Exception as e:
        logging.warning(f"Failed to get resource usage from {instance_name}: {e}")
        return None

# Delete GCP instance
def delete_instance(instance_name):
    try:
        logging.info(f"Deleting instance {instance_name}")
        
        # Get credentials
        credentials = service_account.Credentials.from_service_account_file(
            GCP_CREDENTIALS_FILE, 
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        
        # Create client
        instance_client = compute_v1.InstancesClient(credentials=credentials)
        
        # Delete instance
        operation = instance_client.delete(
            project=GCP_PROJECT_ID,
            zone=GCP_ZONE,
            instance=instance_name
        )
        
        # Wait for operation to complete
        op_client = compute_v1.ZoneOperationsClient(credentials=credentials)
        
        wait_for_operation = True
        while wait_for_operation:
            result = op_client.get(
                project=GCP_PROJECT_ID,
                zone=GCP_ZONE,
                operation=operation.name
            )
            if result.status == compute_v1.Operation.Status.DONE:
                if result.error:
                    for error in result.error.errors:
                        logging.error(f"Error deleting instance: {error.code}: {error.message}")
                    return False
                wait_for_operation = False
            else:
                time.sleep(5)
        
        logging.info(f"Instance {instance_name} deleted successfully")
        return True
    except Exception as e:
        logging.error(f"Failed to delete instance {instance_name}: {e}")
        return False

# Check and scale down instances
def check_and_scale_down():
    global created_instances
    if not created_instances:
        return
    
    logging.info(f"Checking {len(created_instances)} GCP instances for scale-down")
    instances_to_keep = []
    
    for instance in created_instances:
        instance_name = instance['name']
        instance_ip = instance['ip']
        creation_time = instance['created_at']
        
        # Minimum runtime of 30 minutes before considering scale-down
        if time.time() - creation_time < 1800:  # 30 minutes
            logging.info(f"Instance {instance_name} is too new for scale-down (< 30 min)")
            instances_to_keep.append(instance)
            continue
        
        # Get current usage
        usage = get_cloud_instance_usage(instance_name, instance_ip)
        
        if usage is None:
            logging.warning(f"Could not get usage for {instance_name}, keeping instance")
            instances_to_keep.append(instance)
        elif usage < SCALE_DOWN_THRESHOLD:
            logging.info(f"Instance {instance_name} usage {usage}% is below threshold {SCALE_DOWN_THRESHOLD}%, scaling down")
            if delete_instance(instance_name):
                logging.info(f"Successfully scaled down instance {instance_name}")
            else:
                logging.error(f"Failed to scale down instance {instance_name}, keeping in list")
                instances_to_keep.append(instance)
        else:
            logging.info(f"Instance {instance_name} usage {usage}% is above threshold {SCALE_DOWN_THRESHOLD}%, keeping running")
            instances_to_keep.append(instance)
    
    # Update instance list
    created_instances = instances_to_keep
    save_instances()

# Remove SSH known host for IP
def remove_ssh_known_host(ip_address):
    try:
        logging.info(f"Removing SSH known host entry for {ip_address}")
        subprocess.run(
            ["ssh-keygen", "-f", os.path.expanduser("~/.ssh/known_hosts"), "-R", ip_address],
            capture_output=True  # Suppress output
        )
        return True
    except Exception as e:
        logging.error(f"Failed to remove SSH known host: {e}")
        return False

# Check if apt is busy and wait
def wait_for_apt(ip_address, username, max_wait=300):
    logging.info(f"Checking if apt is busy on {ip_address}...")
    
    start_time = time.time()
    while time.time() - start_time < max_wait:
        try:
            # Use a different approach that doesn't rely on grep's exit code
            result = subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no", f"{username}@{ip_address}", 
                 "ps aux | grep -E 'apt|dpkg' | grep -v grep || echo 'Not running'"],
                check=False, capture_output=True, text=True, timeout=10
            )
            
            output = result.stdout.strip()
            if output == "Not running" or not output:
                logging.info("Apt package manager is not busy")
                return True
            else:
                logging.info(f"Apt/dpkg is busy, waiting...")
                logging.debug(f"Busy processes: {output}")
                time.sleep(10)
        except Exception as e:
            logging.warning(f"Error checking apt status: {e}")
            time.sleep(5)
    
    logging.warning(f"Timed out waiting for apt to be available after {max_wait} seconds")
    
    # If we timed out, we'll try to continue anyway
    return True

# Ensure SSH firewall rule exists
def ensure_ssh_firewall_rule():
    try:
        # Get credentials
        credentials = service_account.Credentials.from_service_account_file(
            GCP_CREDENTIALS_FILE, 
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        
        # Get firewall client
        firewall_client = compute_v1.FirewallsClient(credentials=credentials)
        
        # Check if SSH firewall rule exists
        try:
            firewall_client.get(
                project=GCP_PROJECT_ID,
                firewall="allow-ssh"
            )
            logging.info("SSH firewall rule already exists")
            return True
        except Exception:
            logging.info("Creating SSH firewall rule")
            
            # Create firewall rule
            firewall_rule = compute_v1.Firewall()
            firewall_rule.name = "allow-ssh"
            firewall_rule.network = "global/networks/default"
            
            allowed = compute_v1.Allowed()
            allowed.I_p_protocol = "tcp"
            allowed.ports = ["22"]
            
            firewall_rule.allowed = [allowed]
            firewall_rule.source_ranges = ["0.0.0.0/0"]  # Allow from anywhere
            
            operation = firewall_client.insert(
                project=GCP_PROJECT_ID,
                firewall_resource=firewall_rule
            )
            
            # Wait for operation to complete
            op_client = compute_v1.GlobalOperationsClient(credentials=credentials)
            wait_for_operation = True
            while wait_for_operation:
                result = op_client.get(
                    project=GCP_PROJECT_ID,
                    operation=operation.name
                )
                if result.status == compute_v1.Operation.Status.DONE:
                    if result.error:
                        for error in result.error.errors:
                            logging.error(f"Error creating firewall rule: {error.code}: {error.message}")
                        return False
                    wait_for_operation = False
                else:
                    time.sleep(1)
            
            logging.info("SSH firewall rule created successfully")
            return True
    except Exception as e:
        logging.error(f"Failed to ensure SSH firewall rule: {e}")
        return False

# Add HTTP firewall rule
def ensure_http_firewall_rule():
    try:
        # Get credentials
        credentials = service_account.Credentials.from_service_account_file(
            GCP_CREDENTIALS_FILE, 
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        
        # Get firewall client
        firewall_client = compute_v1.FirewallsClient(credentials=credentials)
        
        # Check if HTTP firewall rule exists
        try:
            firewall_client.get(
                project=GCP_PROJECT_ID,
                firewall="allow-http"
            )
            logging.info("HTTP firewall rule already exists")
            return True
        except Exception:
            logging.info("Creating HTTP firewall rule")
            
            # Create firewall rule
            firewall_rule = compute_v1.Firewall()
            firewall_rule.name = "allow-http"
            firewall_rule.network = "global/networks/default"
            
            allowed = compute_v1.Allowed()
            allowed.I_p_protocol = "tcp"
            allowed.ports = ["5000"]  # Flask app port
            
            firewall_rule.allowed = [allowed]
            firewall_rule.source_ranges = ["0.0.0.0/0"]  # Allow from anywhere
            
            operation = firewall_client.insert(
                project=GCP_PROJECT_ID,
                firewall_resource=firewall_rule
            )
            
            # Wait for operation to complete
            op_client = compute_v1.GlobalOperationsClient(credentials=credentials)
            wait_for_operation = True
            while wait_for_operation:
                result = op_client.get(
                    project=GCP_PROJECT_ID,
                    operation=operation.name
                )
                if result.status == compute_v1.Operation.Status.DONE:
                    if result.error:
                        for error in result.error.errors:
                            logging.error(f"Error creating firewall rule: {error.code}: {error.message}")
                        return False
                    wait_for_operation = False
                else:
                    time.sleep(1)
            
            logging.info("HTTP firewall rule created successfully")
            return True
    except Exception as e:
        logging.error(f"Failed to ensure HTTP firewall rule: {e}")
        return False

# Add SSH key to project metadata
def add_ssh_key_to_project_metadata():
    try:
        logging.info("Adding SSH key to project metadata")
        
        # Generate SSH key if it doesn't exist
        ssh_key_path = os.path.expanduser("~/.ssh/id_rsa")
        if not os.path.exists(ssh_key_path):
            logging.info("Generating SSH key...")
            subprocess.run(
                ["ssh-keygen", "-t", "rsa", "-b", "2048", "-f", ssh_key_path, "-N", ""],
                check=True
            )
        
        # Read public key
        with open(os.path.expanduser("~/.ssh/id_rsa.pub"), "r") as f:
            public_key = f.read().strip()
        
        # Format key for GCP
        username = os.environ.get("USER", "ubuntu")
        formatted_key = f"{username}:{public_key}"
        
        # Use the GCP Python client library directly instead of REST API
        credentials = service_account.Credentials.from_service_account_file(
            GCP_CREDENTIALS_FILE, 
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        
        # Create a projects client
        projects_client = compute_v1.ProjectsClient(credentials=credentials)
        
        # Get current project metadata
        project = projects_client.get(project=GCP_PROJECT_ID)
        
        # Get common instance metadata
        metadata = project.common_instance_metadata
        
        # Check if ssh-keys entry exists
        ssh_keys_exist = False
        items = list(metadata.items or [])
        for item in items:
            if item.key == "ssh-keys":
                item.value = f"{item.value or ''}\n{formatted_key}"
                ssh_keys_exist = True
                break
        
        if not ssh_keys_exist:
            items.append(compute_v1.Items(
                key="ssh-keys",
                value=formatted_key
            ))
        
        # Update metadata
        metadata.items = items
        
        # Set project metadata
        operation = projects_client.set_common_instance_metadata(
            project=GCP_PROJECT_ID,
            metadata_resource=metadata
        )
        
        # Wait for operation to complete
        global_operations_client = compute_v1.GlobalOperationsClient(credentials=credentials)
        
        wait_for_operation = True
        while wait_for_operation:
            result = global_operations_client.get(
                project=GCP_PROJECT_ID,
                operation=operation.name
            )
            if result.status == compute_v1.Operation.Status.DONE:
                if result.error:
                    for error in result.error.errors:
                        logging.error(f"Error setting project metadata: {error.code}: {error.message}")
                    return False
                wait_for_operation = False
            else:
                time.sleep(1)
        
        logging.info("SSH key added to project metadata successfully")
        return True
    except Exception as e:
        logging.error(f"Failed to add SSH key to project metadata: {e}")
        logging.info("Will continue without adding SSH key to project metadata")
        return False

# Create GCP VM instance
def create_gcp_instance(client):
    try:
        # First, ensure SSH firewall rule exists
        ensure_ssh_firewall_rule()
        
        # Ensure HTTP firewall rule for app access
        ensure_http_firewall_rule()
        
        # Add SSH key to project metadata
        add_ssh_key_to_project_metadata()
        
        logging.info(f"Starting GCP instance creation in project {GCP_PROJECT_ID}, zone {GCP_ZONE}")
        machine_type = f"zones/{GCP_ZONE}/machineTypes/{GCP_MACHINE_TYPE}"
        hostname = socket.gethostname()
        # Create VM name that complies with GCP naming requirements (lowercase letters, numbers, hyphens)
        instance_name = f"vm-{hostname.lower()}-{int(time.time())}"
        
        # Use direct image reference instead of API call
        source_image = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts"
        logging.info(f"Using Ubuntu image: {source_image}")
        
        # Create instance
        instance_request = compute_v1.Instance()
        instance_request.name = instance_name
        instance_request.machine_type = machine_type
        
        # Configure disk
        disk = compute_v1.AttachedDisk()
        disk.boot = True
        disk.auto_delete = True
        
        disk_init = compute_v1.AttachedDiskInitializeParams()
        disk_init.source_image = source_image
        disk_init.disk_size_gb = 20
        disk_init.disk_type = f"zones/{GCP_ZONE}/diskTypes/pd-balanced"
        
        disk.initialize_params = disk_init
        instance_request.disks = [disk]
        
        # Configure network
        network_interface = compute_v1.NetworkInterface()
        network_interface.network = "global/networks/default"
        
        access_config = compute_v1.AccessConfig()
        access_config.name = "External NAT"
        access_config.type_ = "ONE_TO_ONE_NAT"
        access_config.network_tier = "PREMIUM"
        
        network_interface.access_configs = [access_config]
        instance_request.network_interfaces = [network_interface]
        
        # Add metadata
        metadata = compute_v1.Metadata()
        items = []
        
        # Create a more robust startup script
        startup_script = """#!/bin/bash
# Install required packages - note the pre-installed python3-pip
apt-get update
apt-get install -y rsync openssh-server python3-pip

# Flag file to indicate setup completion
touch /tmp/startup_complete

# Enable and start SSH
systemctl enable ssh
systemctl start ssh

# Create ubuntu user if it doesn't exist
if ! id -u ubuntu > /dev/null 2>&1; then
    useradd -m -s /bin/bash ubuntu
    echo "ubuntu ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/ubuntu
    chmod 0440 /etc/sudoers.d/ubuntu
fi

# Setup SSH directories
mkdir -p /home/ubuntu/.ssh
"""

        # Add SSH keys to the startup script
        try:
            with open(os.path.expanduser("~/.ssh/id_rsa.pub"), "r") as f:
                public_key = f.read().strip()
                
            # Append the SSH key setup to the startup script
            startup_script += f"""
echo "{public_key}" >> /home/ubuntu/.ssh/authorized_keys
chmod 700 /home/ubuntu/.ssh
chmod 600 /home/ubuntu/.ssh/authorized_keys
chown -R ubuntu:ubuntu /home/ubuntu/.ssh
"""
            
            # Also try to add the SSH key directly to instance metadata
            items.append(compute_v1.Items(
                key="ssh-keys",
                value=f"ubuntu:{public_key}"
            ))
        except Exception as e:
            logging.error(f"Failed to read SSH key for startup script: {e}")
            
        # Add the script to metadata
        items.append(compute_v1.Items(
            key="startup-script",
            value=startup_script
        ))
        
        metadata.items = items
        instance_request.metadata = metadata
        
        # Create the instance
        logging.info(f"Sending request to create instance {instance_name}")
        operation = client.insert(
            project=GCP_PROJECT_ID,
            zone=GCP_ZONE,
            instance_resource=instance_request
        )
        
        # Get Zone Operations client using the same credentials
        credentials = service_account.Credentials.from_service_account_file(
            GCP_CREDENTIALS_FILE, 
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        op_client = compute_v1.ZoneOperationsClient(credentials=credentials)
        
        logging.info(f"Waiting for instance creation operation to complete...")
        
        # Wait for operation to complete
        wait_for_operation = True
        while wait_for_operation:
            try:
                result = op_client.get(
                    project=GCP_PROJECT_ID,
                    zone=GCP_ZONE,
                    operation=operation.name
                )
                if result.status == compute_v1.Operation.Status.DONE:
                    if result.error:
                        for error in result.error.errors:
                            logging.error(f"Error creating instance: {error.code}: {error.message}")
                        return None
                    wait_for_operation = False
                else:
                    time.sleep(5)
            except Exception as e:
                logging.error(f"Error checking operation status: {e}")
                time.sleep(5)
        
        logging.info(f"GCP Instance {instance_name} created successfully")
        return instance_name
    except Exception as e:
        logging.error(f"Failed to create GCP instance: {e}")
        return None

# Deploy application to cloud VM
def deploy_to_cloud(instance_name):
    try:
        logging.info(f"Preparing to deploy application to instance {instance_name}")
        
        # Create credentials for the instance client
        credentials = service_account.Credentials.from_service_account_file(
            GCP_CREDENTIALS_FILE, 
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        
        # Get instance details
        gcp_client = compute_v1.InstancesClient(credentials=credentials)
        
        # Wait for instance to be fully ready
        instance_ready = False
        retries = 30  # Increased retries
        instance = None
        
        while not instance_ready and retries > 0:
            try:
                instance = gcp_client.get(
                    project=GCP_PROJECT_ID,
                    zone=GCP_ZONE,
                    instance=instance_name
                )
                
                if instance.status == "RUNNING":
                    instance_ready = True
                    logging.info(f"Instance {instance_name} is now running")
                else:
                    logging.info(f"Instance status: {instance.status}, waiting...")
                    time.sleep(10)
                    retries -= 1
            except Exception as e:
                logging.warning(f"Error checking instance status: {e}")
                time.sleep(10)
                retries -= 1
        
        if not instance_ready:
            logging.error(f"Instance {instance_name} did not reach RUNNING state in time")
            return False, None
        
        # Get instance IP using REST API
        external_ip = None
        try:
            # Get a new access token
            auth_request = google.auth.transport.requests.Request()
            credentials.refresh(auth_request)
            
            # Use REST API to get instance details
            headers = {
                "Authorization": f"Bearer {credentials.token}"
            }
            response = requests.get(
                f"https://compute.googleapis.com/compute/v1/projects/{GCP_PROJECT_ID}/zones/{GCP_ZONE}/instances/{instance_name}",
                headers=headers
            )
            response.raise_for_status()
            instance_data = response.json()
            
            # Extract IP address
            external_ip = instance_data["networkInterfaces"][0]["accessConfigs"][0]["natIP"]
            logging.info(f"Instance external IP: {external_ip}")
        except Exception as e:
            logging.error(f"Failed to get external IP: {e}")
            return False, None

        if not external_ip:
            logging.error("Could not determine external IP for the instance")
            return False, None
            
        # Remove the host key for this IP to avoid warnings
        remove_ssh_known_host(external_ip)
        
        # Wait for startup script to complete - additional wait time
        logging.info(f"Waiting 60 seconds for startup script to complete...")
        time.sleep(60)
        
        # Wait longer for SSH to be available (up to 10 minutes)
        logging.info(f"Waiting for SSH to become available on {external_ip}...")
        ssh_available = False
        username = "ubuntu"  # Default username
        
        for attempt in range(60):  # Try for 10 minutes (60 * 10s)
            try:
                logging.info(f"SSH connection attempt {attempt+1}/60")
                result = subprocess.run(
                    ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", 
                     f"ubuntu@{external_ip}", "echo 'SSH is available'"],
                    check=True, capture_output=True, text=True, timeout=15
                )
                logging.info(f"SSH connection successful: {result.stdout.strip()}")
                ssh_available = True
                break
            except subprocess.CalledProcessError as e:
                logging.warning(f"SSH connection failed: {e}")
                time.sleep(10)
            except subprocess.TimeoutExpired:
                logging.warning("SSH connection timed out")
                time.sleep(10)
        
        if not ssh_available:
            logging.error(f"SSH to {external_ip} not available after 10 minutes")
            
            # Try with different username as fallback
            logging.info("Trying with 'hari2' username instead of 'ubuntu'...")
            for attempt in range(10):
                try:
                    result = subprocess.run(
                        ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10", 
                         f"hari2@{external_ip}", "echo 'SSH is available'"],
                        check=True, capture_output=True, text=True, timeout=15
                    )
                    logging.info(f"SSH connection with username 'hari2' successful: {result.stdout.strip()}")
                    ssh_available = True
                    
                    # Update username for further operations
                    username = "hari2"
                    break
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    time.sleep(10)
            
            if not ssh_available:
                return False, None
        
        # Wait for apt to finish any ongoing operations
        wait_for_apt(external_ip, username, max_wait=300)
        
        # Copy application files
        logging.info(f"Copying application files to {external_ip}...")
        try:
            # Use -o StrictHostKeyChecking=no to avoid host key verification issues
            subprocess.run(
                ["rsync", "-avz", "-e", "ssh -o StrictHostKeyChecking=no", 
                 f"{APPLICATION_PATH}/", f"{username}@{external_ip}:~/application"],
                check=True, timeout=300  # 5 minute timeout for rsync
            )
        except Exception as e:
            logging.error(f"Failed to copy application files: {e}")
            return False, None
        
        # Install dependencies and start application with retry logic
        setup_commands = [
            "sudo apt-get update",
            "sudo DEBIAN_FRONTEND=noninteractive apt-get -y install python3-pip",
            "cd ~/application && pip3 install -r requirements.txt",
            "cd ~/application && nohup python3 app.py > app.log 2>&1 &"
        ]
        
        for cmd in setup_commands:
            logging.info(f"Running command: {cmd}")
            max_retries = 5
            for retry in range(max_retries):
                try:
                    # Check if dpkg/apt is available before executing command
                    if "apt-get" in cmd:
                        wait_for_apt(external_ip, username)
                        
                    # Execute the command with StrictHostKeyChecking=no
                    subprocess.run(
                        ["ssh", "-o", "StrictHostKeyChecking=no", f"{username}@{external_ip}", cmd],
                        check=True, timeout=300  # 5 minute timeout per command
                    )
                    break  # Command succeeded, break retry loop
                except Exception as e:
                    if retry < max_retries - 1:
                        logging.warning(f"Command '{cmd}' failed, retrying ({retry+1}/{max_retries}): {e}")
                        time.sleep(30)  # Wait longer between retries
                    else:
                        logging.error(f"Failed to run command '{cmd}' after {max_retries} attempts: {e}")
                        return False, None
        
        logging.info(f"Application deployed successfully to {instance_name} ({external_ip})")
        
        # Track this instance for potential scaling down later
        global created_instances
        created_instances.append({
            'name': instance_name,
            'ip': external_ip,
            'created_at': time.time(),
            'username': username
        })
        save_instances()
        
        return True, external_ip
    except Exception as e:
        logging.error(f"Failed to deploy application: {e}")
        return False, None

# Main monitoring loop
def main():
    logging.info("Starting VM resource monitoring and auto-scaling service")
    
    # Load previously created instances
    load_instances()
    
    # Initialize GCP client
    gcp_client = init_gcp_client()
    if not gcp_client:
        logging.error("Failed to initialize GCP client. Exiting.")
        return
    
    last_scale_time = 0
    scale_cooldown = 3600  # 1 hour cooldown between scaling operations
    last_scale_down_check = 0
    scale_down_interval = 300  # Check for scale down every 5 minutes
    
    while True:
        try:
            # Get resource usage
            usage = get_resource_usage()
            
            # Check if threshold is exceeded for scaling up
            if usage["average"] > THRESHOLD:
                logging.warning(f"Resource usage ({usage['average']}%) exceeds threshold ({THRESHOLD}%)")
                
                # Check if we're within cooldown period
                current_time = time.time()
                if current_time - last_scale_time > scale_cooldown:
                    logging.info("Initiating auto-scaling to GCP")
                    
                    # Create GCP instance
                    instance_name = create_gcp_instance(gcp_client)
                    if instance_name:
                        # Deploy application to cloud
                        success, ip = deploy_to_cloud(instance_name)
                        if success:
                            last_scale_time = current_time
                            logging.info(f"Auto-scaling and deployment completed successfully to {ip}")
                        else:
                            logging.error("Failed to deploy application to cloud instance")
                else:
                    logging.info(f"Within cooldown period ({(current_time - last_scale_time)/60:.1f} minutes since last scaling)")
            
            # Check if it's time to check for scaling down
            current_time = time.time()
            if current_time - last_scale_down_check > scale_down_interval:
                logging.info("Checking for instances to scale down")
                check_and_scale_down()
                last_scale_down_check = current_time
            
            # Wait for next check
            time.sleep(CHECK_INTERVAL)
            
        except Exception as e:
            logging.error(f"Error in monitoring loop: {e}")
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
