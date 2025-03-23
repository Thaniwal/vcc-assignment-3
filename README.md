# VM Auto-Scaling Project

This project sets up a local VM with resource monitoring and auto-scaling capabilities to GCP when resource usage exceeds 75%.

## Components

- **Local VM Setup**: Using VirtualBox with Ubuntu 22.04
- **Resource Monitoring**: Python script using psutil for resource tracking
- **Auto-Scaling**: Automatic deployment to GCP when resource usage threshold is exceeded
- **Sample Application**: Flask web application with load generation capabilities

## Directory Structure

- `/monitoring`: Contains the resource monitoring and auto-scaling script
- `/application`: Sample Flask application for testing
- `/docs`: Documentation and architecture diagrams


