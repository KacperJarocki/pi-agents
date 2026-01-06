# pi-agents

## Docker Build Workflow

This repository includes a GitHub Action workflow for building multi-platform Docker images from any directory containing a Dockerfile.

### Features

- **Multi-platform builds**: Automatically builds for both `linux/amd64` and `linux/arm64` architectures
- **Flexible directory selection**: Build from any directory in the repository
- **Customizable naming**: Configure image name and tag as needed

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

This will execute: `docker buildx build --platform linux/amd64,linux/arm64 -t my-app:v1.0.0 ./backend`

The image will be built for both AMD64 and ARM64 architectures.