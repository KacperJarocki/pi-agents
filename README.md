# pi-agents

## Docker Build Workflow

This repository includes a GitHub Action workflow for building and pushing multi-platform Docker images from any directory containing a Dockerfile.

### Features

- **Multi-platform builds**: Automatically builds for both `linux/amd64` and `linux/arm64` architectures
- **Multi-arch manifest**: Creates a single image tag that automatically serves the correct architecture
- **Automatic image naming**: Defaults image name to the directory name if not specified
- **Registry push**: Pushes built images to Docker registry (Docker Hub, GHCR, etc.)
- **Flexible directory selection**: Build from any directory in the repository
- **Customizable naming**: Configure image name and tag as needed

### How to Use

1. Navigate to the **Actions** tab in this repository
2. Select the **Docker Build** workflow from the left sidebar
3. Click the **Run workflow** button
4. Fill in the parameters:
   - **Directory containing the Dockerfile to build**: Path to the directory (e.g., `.`, `./app`, `./services/api`)
   - **Docker registry**: Registry URL (e.g., `docker.io`, `ghcr.io`)
   - **Docker registry username**: Your registry username
   - **Docker image name** (optional): Name for your Docker image (defaults to directory name)
   - **Docker image tag** (optional): Tag for your Docker image (default: `latest`)
5. Click **Run workflow** to start the build

**Note**: You must set up a `DOCKER_PASSWORD` secret in your repository settings containing your Docker registry password or token.

### Example

To build a Dockerfile located in `./backend`:
- Directory: `./backend`
- Registry: `docker.io`
- Registry username: `myusername`
- Image name: (leave empty to use `backend`)
- Image tag: `v1.0.0`

This will execute: `docker buildx build --platform linux/amd64,linux/arm64 --push ...`

The image `docker.io/myusername/backend:v1.0.0` will be pushed with a multi-arch manifest.

When you pull this image, Docker automatically selects the correct architecture (AMD64 or ARM64) for your platform.