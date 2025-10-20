# Web Grading Interface - Docker Deployment

This directory contains Docker configuration for deploying the Web Grading Interface.

## Quick Start

### Prerequisites

- **Docker** installed ([Get Docker](https://docs.docker.com/get-docker/))
- **Docker Compose** installed (usually included with Docker Desktop)
- API keys for Canvas and AI services

### Setup Steps

1. **Navigate to this directory:**
   ```bash
   cd docker/web-grading
   ```

2. **Copy the environment template:**
   ```bash
   cp .env.example .env
   ```

   **Alternative:** If you already have credentials in `~/.env`:
   ```bash
   cp ~/.env .env
   ```

3. **Edit `.env` and add your credentials (if starting from template):**
   ```bash
   nano .env  # or use your preferred editor
   ```

   Required values:
   - `CANVAS_API_KEY_PROD` - Your Canvas API key
   - `CANVAS_API_URL_PROD` - Your Canvas instance URL
   - `ANTHROPIC_API_KEY` - Your Anthropic API key

   See [Getting API Keys](#getting-api-keys) below for details.

4. **Build and start the application:**
   ```bash
   docker-compose build  # First build takes 2-3 minutes
   docker-compose up -d
   ```

5. **Verify it's running:**
   ```bash
   # Wait a few seconds for startup, then check:
   curl http://localhost:8000/api/health
   # Should return: {"status":"healthy","version":"..."}

   # Or check the logs:
   docker-compose logs web
   # Should see: "Uvicorn running on http://0.0.0.0:8000"
   ```

6. **Access the application:**

   Open your browser to: **http://localhost:8000**

   You should see the Web Grading Interface home screen with options to:
   - Create a new grading session
   - Resume an existing session
   - Import a saved session

## Getting API Keys

### Canvas API Key

1. Log into your Canvas instance
2. Navigate to **Account → Settings**
3. Scroll down to **Approved Integrations**
4. Click **+ New Access Token**
5. Enter a purpose (e.g., "Web Grading Interface")
6. Click **Generate Token**
7. **Copy the token immediately** (you won't see it again!)
8. Paste into `.env` as `CANVAS_API_KEY_PROD`

**Canvas URL:**
- Production usually: `https://[your-institution].instructure.com`
- Beta/Test usually: `https://[your-institution].beta.instructure.com`

### Anthropic API Key

1. Go to https://console.anthropic.com/
2. Sign up or log in
3. Navigate to **API Keys**
4. Click **Create Key**
5. Copy the key (starts with `sk-ant-...`)
6. Paste into `.env` as `ANTHROPIC_API_KEY`

### OpenAI API Key (Optional)

1. Go to https://platform.openai.com/api-keys
2. Sign up or log in
3. Click **Create new secret key**
4. Copy the key (starts with `sk-...`)
5. Paste into `.env` as `OPENAI_API_KEY`

## Common Commands

### Start the application
```bash
docker-compose up -d
```

### Stop the application
```bash
docker-compose down
```

### View logs
```bash
docker-compose logs -f web
```

### Restart after making changes
```bash
docker-compose restart
```

### Rebuild after code updates
```bash
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### Access container shell (for debugging)
```bash
docker-compose exec web bash
```

### View container status
```bash
docker-compose ps
```

## Data Persistence

The application stores data in a Docker volume named `grading-data`. This persists across container restarts and rebuilds.

### Backup Database

```bash
# Create backup
docker-compose exec web cp /data/grading.db /data/grading.db.backup
docker cp $(docker-compose ps -q web):/data/grading.db.backup ./backup-$(date +%Y%m%d).db

# Or use a one-liner:
docker-compose exec web cat /data/grading.db > backup-$(date +%Y%m%d).db
```

### Restore Database

```bash
docker cp backup.db $(docker-compose ps -q web):/data/grading.db
docker-compose restart
```

### View Database Location

```bash
docker volume inspect web-grading_grading-data
```

## Troubleshooting

### Container won't start

**Check logs:**
```bash
docker-compose logs web
```

**Common issues:**
- Missing `.env` file → Copy from `.env.example`
- Invalid API keys → Check keys are correct and have no extra spaces
- Port 8000 already in use → Stop other services or change port in `docker-compose.yml`

### Can't access at localhost:8000

**Check if container is running:**
```bash
docker-compose ps
```

**Check health status:**
```bash
docker inspect autograder-web-grading | grep -A 10 Health
```

**Try accessing health endpoint directly:**
```bash
curl http://localhost:8000/api/health
```

**Check firewall:**
- Ensure Docker Desktop is allowed through firewall
- Try accessing from `http://127.0.0.1:8000` instead

### Database errors

**Reset database (WARNING: deletes all data):**
```bash
docker-compose down -v  # -v removes volumes
docker-compose up -d
```

**Check database file:**
```bash
docker-compose exec web ls -lh /data/
```

### API key errors

**Verify environment variables are loaded:**
```bash
docker-compose exec web env | grep CANVAS
docker-compose exec web env | grep ANTHROPIC
```

**Test Canvas connection:**
```bash
docker-compose exec web python -c "
from lms_interface.canvas_interface import CanvasInterface
canvas = CanvasInterface(prod=True)
print('Canvas connection successful!')
courses = list(canvas.canvas.get_courses())
print(f'Found {len(courses)} courses')
"
```

### Permission errors

**Fix permissions on volumes:**
```bash
docker-compose exec web chown -R $(id -u):$(id -g) /data
```

### Out of disk space

**Clean up Docker resources:**
```bash
docker system prune -a
docker volume prune
```

**Check disk usage:**
```bash
docker system df
```

## Configuration

### Changing the Port

Edit `docker-compose.yml`:
```yaml
ports:
  - "8080:8000"  # Change first number only
```

Then restart:
```bash
docker-compose down && docker-compose up -d
```

### Using Existing ~/.env File

If you already have credentials in `~/.env`, you can mount it instead:

Create `docker-compose.override.yml`:
```yaml
version: '3.8'
services:
  web:
    volumes:
      - ~/.env:/app/.env:ro
```

This file is git-ignored and overrides the default configuration.

### Custom Database Location

To store the database outside Docker volumes:

Edit `docker-compose.yml`:
```yaml
volumes:
  - /path/to/your/data:/data
```

## Security Notes

- **Never commit `.env` files** to version control
- Keep API keys secure and rotate them periodically
- Use **development Canvas** (`CANVAS_API_URL_DEV`) for testing
- Only use production Canvas when you're ready to submit real grades
- The database contains student data - ensure it's backed up and secured
- Consider using Docker secrets for production deployments

## System Requirements

### Minimum
- 2 CPU cores
- 4 GB RAM
- 10 GB disk space
- Docker 20.10+

### Recommended
- 4 CPU cores
- 8 GB RAM
- 50 GB disk space (for large numbers of PDFs)
- SSD for better performance

## Dependencies

All Python dependencies are managed through `pyproject.toml` in the repository root. The Docker image automatically installs:

- **Core dependencies**: FastAPI, uvicorn, PyMuPDF, Pillow, opencv-python
- **LMS integration**: lms-interface (local path dependency)
- **AI services**: anthropic, openai
- **Database**: aiosqlite for async SQLite
- **Image processing**: opencv-python, pyzbar, Pillow
- **Text matching**: fuzzywuzzy with python-Levenshtein for performance

System libraries for OpenCV are automatically installed in the Docker image.

## Platform-Specific Notes

### macOS
- Docker Desktop required
- Performance may be slower than Linux due to filesystem virtualization
- Ensure Docker Desktop has sufficient resource allocation (Preferences → Resources)

### Linux
- Native Docker performance (fastest)
- May need to add user to `docker` group:
  ```bash
  sudo usermod -aG docker $USER
  ```
- Log out and back in for group changes to take effect

### Windows
- Docker Desktop with WSL2 backend recommended
- PowerShell or WSL terminal required
- Ensure WSL2 is up to date
- Line endings: Use LF, not CRLF (configure Git: `git config --global core.autocrlf input`)

## Updating

### Pull Latest Code
```bash
cd /path/to/Autograder
git pull
cd docker/web-grading
```

### Rebuild and Restart
```bash
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### Database Migrations

Database migrations are automatic. When you update the code and restart, the application will automatically upgrade the database schema.

**Always backup before updating:**
```bash
docker-compose exec web cat /data/grading.db > backup-before-update.db
```

## Getting Help

### Documentation
- Main documentation: `../../Autograder/web_grading/docs/`
- User guide: `../../Autograder/web_grading/docs/user-guide.md`
- Architecture: `../../Autograder/web_grading/docs/architecture.md`

### Support
- Check logs: `docker-compose logs -f web`
- Health check: http://localhost:8000/api/health
- API docs: http://localhost:8000/api/docs

### Reporting Issues
When reporting issues, please include:
1. Output of `docker-compose logs web`
2. Output of `docker-compose ps`
3. Your OS and Docker version: `docker --version`
4. Steps to reproduce the problem

## Uninstalling

### Stop and Remove Containers
```bash
docker-compose down
```

### Remove Images
```bash
docker rmi autograder-web-grading:latest
```

### Remove Volumes (WARNING: Deletes all data)
```bash
docker volume rm web-grading_grading-data
```

### Complete Cleanup
```bash
cd docker/web-grading
docker-compose down -v --rmi all
```

## Advanced Usage

### Running in Production

For production deployment:
1. Use a proper domain name (not localhost)
2. Set up SSL/TLS with nginx or Caddy
3. Use Docker secrets instead of `.env` file
4. Set up automated backups
5. Configure monitoring and alerting
6. Use a dedicated server or cloud instance

See `../../Autograder/web_grading/planning/deployment-roadmap.md` for production deployment guide.

### Development Mode

To develop with hot-reload:
```bash
# Run directly without Docker
cd ../../Autograder/web_grading
python -m uvicorn web_api.main:app --reload
```

Or mount source code:
```yaml
# docker-compose.override.yml
services:
  web:
    volumes:
      - ../../Autograder:/app/Autograder
    command: python -m uvicorn web_api.main:app --reload --host 0.0.0.0
```

## Architecture

```
┌─────────────────────────────────────────┐
│   Browser (localhost:8000)              │
└────────────────┬────────────────────────┘
                 │ HTTP
┌────────────────▼────────────────────────┐
│   Docker Container                      │
│   ┌─────────────────────────────────┐   │
│   │  FastAPI App (port 8000)        │   │
│   │  - Web API                       │   │
│   │  - Static files                  │   │
│   └─────────────┬───────────────────┘   │
│                 │                        │
│   ┌─────────────▼───────────────────┐   │
│   │  SQLite Database                │   │
│   │  /data/grading.db               │   │
│   └─────────────────────────────────┘   │
└─────────────────────────────────────────┘
         │                  │
         │                  │
         ▼                  ▼
  Canvas API      Anthropic/OpenAI API
  (external)         (external)
```

## License

See main project LICENSE file.

## Contributing

See main project CONTRIBUTING.md for guidelines.
