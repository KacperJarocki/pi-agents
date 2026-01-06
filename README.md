# pi-agents

## Docker Build Workflow

This repository includes a GitHub Action workflow for building Docker images from any directory containing a Dockerfile.

### How to Use

1. Navigate to the **Actions** tab in this repository
2. Select the **Docker Build** workflow from the left sidebar
3. Click the **Run workflow** button
4. Fill in the parameters:
   - **Directory containing the Dockerfile to build**: Path to the directory (e.g., `.`, `./app`, `./services/api`)
   - **Docker image name** (optional): Name for your Docker image (default: `my-docker-image`)
   - **Docker image tag** (optional): Tag for your Docker image (default: `latest`)
5. Click **Run workflow** to start the build

### Example

To build a Dockerfile located in `./backend`:
- Directory: `./backend`
- Image name: `my-app`
- Image tag: `v1.0.0`

This will execute: `docker build -t my-app:v1.0.0 ./backend`