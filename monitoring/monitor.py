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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename=os.path.expanduser('~/vm-autoscaler/autoscaler.log'),
    filemode='a'
)

# Configuration
THRESHOLD = 50  # 50% resource usage threshold
CHECK_INTERVAL = 30  # Check every 30 seconds
GCP_PROJECT_ID = "vcc-assignment-3-454613"
GCP_ZONE = "us-central1-a"
GCP_MACHINE_TYPE = "e2-standard-2"
GCP_CREDENTIALS_FILE = "/home/hari2/vm-autoscaler/vcc-assignment-3-454613-712c2b41187f.json"
APPLICATION_PATH = "/home/hari2/application"

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

# Create GCP VM instance
def create_gcp_instance(client):
    try:
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
        
        # Add startup script to install necessary packages
        metadata = compute_v1.Metadata()
        items = []
        
        startup_script = """#!/bin/bash
apt-get update
apt-get install -y python3-pip rsync
"""
        
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
        retries = 20
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
            return False
        
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
            return False

        if not external_ip:
            logging.error("Could not determine external IP for the instance")
            return False
        
        # Generate SSH key if it doesn't exist
        ssh_key_path = os.path.expanduser("~/.ssh/id_rsa")
        if not os.path.exists(ssh_key_path):
            logging.info("Generating SSH key...")
            subprocess.run(
                ["ssh-keygen", "-t", "rsa", "-b", "2048", "-f", ssh_key_path, "-N", ""],
                check=True
            )
        
        # Add SSH key to instance
        try:
            with open(os.path.expanduser("~/.ssh/id_rsa.pub"), "r") as f:
                public_key = f.read().strip()
            
            # Format key for GCP
            username = os.environ.get("USER", "ubuntu")
            formatted_key = f"{username}:{public_key}"
            
            # Add key using REST API
            current_metadata = None
            
            # Get current metadata
            metadata_response = requests.get(
                f"https://compute.googleapis.com/compute/v1/projects/{GCP_PROJECT_ID}/zones/{GCP_ZONE}/instances/{instance_name}",
                headers=headers
            )
            metadata_response.raise_for_status()
            instance_data = metadata_response.json()
            
            if "metadata" in instance_data and "items" in instance_data["metadata"]:
                current_metadata = instance_data["metadata"]
            else:
                current_metadata = {"items": []}
            
            # Check if there's already an ssh-keys entry
            ssh_keys_exist = False
            for item in current_metadata.get("items", []):
                if item.get("key") == "ssh-keys":
                    item["value"] = item.get("value", "") + "\n" + formatted_key
                    ssh_keys_exist = True
                    break
            
            if not ssh_keys_exist:
                current_metadata["items"].append({
                    "key": "ssh-keys",
                    "value": formatted_key
                })
            
            # Update metadata
            metadata_update = {
                "items": current_metadata.get("items", [])
            }
            
            # Add fingerprint if it exists
            if "fingerprint" in current_metadata:
                metadata_update["fingerprint"] = current_metadata["fingerprint"]
            
            metadata_update_response = requests.patch(
                f"https://compute.googleapis.com/compute/v1/projects/{GCP_PROJECT_ID}/zones/{GCP_ZONE}/instances/{instance_name}/setMetadata",
                headers=headers,
                json=metadata_update
            )
            metadata_update_response.raise_for_status()
            
            logging.info("SSH key added to instance metadata via REST API")
        except Exception as e:
            logging.error(f"Failed to add SSH key to instance metadata: {e}")
            logging.warning("Continuing anyway, SSH key might be available through project metadata")
        
        # Wait for SSH to be available
        logging.info(f"Waiting for SSH to become available on {external_ip}...")
        ssh_available = False
        for attempt in range(30):  # Try for 5 minutes (30 * 10s)
            try:
                logging.info(f"SSH connection attempt {attempt+1}/30")
                result = subprocess.run(
                    ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", 
                     f"ubuntu@{external_ip}", "echo 'SSH is available'"],
                    check=True, capture_output=True, text=True
                )
                logging.info(f"SSH connection successful: {result.stdout.strip()}")
                ssh_available = True
                break
            except subprocess.CalledProcessError as e:
                logging.warning(f"SSH connection failed: {e}")
                time.sleep(10)
        
        if not ssh_available:
            logging.error(f"SSH to {external_ip} not available after 5 minutes")
            return False
        
        # Copy application files
        logging.info(f"Copying application files to {external_ip}...")
        subprocess.run(
            ["rsync", "-avz", "-e", "ssh -o StrictHostKeyChecking=no", 
             f"{APPLICATION_PATH}/", f"ubuntu@{external_ip}:~/application"],
            check=True
        )
        
        # Install dependencies and start application
        setup_commands = [
            "sudo apt-get update",
            "sudo apt-get install -y python3-pip",
            "cd ~/application && pip3 install -r requirements.txt",
            "cd ~/application && nohup python3 app.py > app.log 2>&1 &"
        ]
        
        for cmd in setup_commands:
            logging.info(f"Running command: {cmd}")
            subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no", f"ubuntu@{external_ip}", cmd],
                check=True
            )
        
        logging.info(f"Application deployed successfully to {instance_name} ({external_ip})")
        return True
    except Exception as e:
        logging.error(f"Failed to deploy application: {e}")
        return False

# Main monitoring loop
def main():
    logging.info("Starting VM resource monitoring and auto-scaling service")
    
    # Initialize GCP client
    gcp_client = init_gcp_client()
    if not gcp_client:
        logging.error("Failed to initialize GCP client. Exiting.")
        return
    
    last_scale_time = 0
    scale_cooldown = 3600  # 1 hour cooldown between scaling operations
    
    while True:
        try:
            # Get resource usage
            usage = get_resource_usage()
            
            # Check if threshold is exceeded
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
                        if deploy_to_cloud(instance_name):
                            last_scale_time = current_time
                            logging.info("Auto-scaling and deployment completed successfully")
                        else:
                            logging.error("Failed to deploy application to cloud instance")
                else:
                    logging.info(f"Within cooldown period ({(current_time - last_scale_time)/60:.1f} minutes since last scaling)")
            
            # Wait for next check
            time.sleep(CHECK_INTERVAL)
            
        except Exception as e:
            logging.error(f"Error in monitoring loop: {e}")
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
