# pi-agents

## Docker Build Workflow

This repository includes a GitHub Action workflow for building and pushing multi-platform Docker images from any directory containing a Dockerfile to GitHub Container Registry (ghcr.io).

### Features

- **GitHub Container Registry Integration**: Automatically pushes to ghcr.io and appears in repository Packages
- **Multi-platform builds**: Automatically builds for both `linux/amd64` and `linux/arm64` architectures
- **Multi-arch manifest**: Creates a single image tag that automatically serves the correct architecture
- **Automatic image naming**: Defaults image name to the directory name if not specified (automatically converted to lowercase)
- **GitHub Integration**: Uses GitHub repository owner and repository name for image organization
- **Flexible directory selection**: Build from any directory in the repository
- **Customizable naming**: Configure image name and tag as needed

### How to Use

1. Navigate to the **Actions** tab in this repository
2. Select the **Docker Build** workflow from the left sidebar
3. Click the **Run workflow** button
4. Fill in the parameters:
   - **Directory containing the Dockerfile to build**: Path to the directory (e.g., `.`, `./app`, `./services/api`)
   - **Docker image name** (optional): Name for your Docker image (defaults to directory name or repository name)
   - **Docker image tag** (optional): Tag for your Docker image (default: `latest`)
5. Click **Run workflow** to start the build

**Note**: The workflow uses GitHub's built-in `GITHUB_TOKEN` which has the necessary permissions. No additional secrets need to be configured.

### Example

To build a Dockerfile located in `./backend`:
- Directory: `./backend`
- Image name: (leave empty to use `backend`)
- Image tag: `v1.0.0`

This will execute: `docker buildx build --platform linux/amd64,linux/arm64 --push -t ghcr.io/owner/backend:v1.0.0 ./backend`

The image `ghcr.io/owner/backend:v1.0.0` will be pushed with a multi-arch manifest and will be visible in your repository's Packages section.

**Note**: Owner and image names are automatically converted to lowercase to comply with Docker/GHCR naming requirements.

When you pull this image, Docker automatically selects the correct architecture (AMD64 or ARM64) for your platform.