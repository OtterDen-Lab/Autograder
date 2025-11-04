#!/bin/sh
set -e

echo "Starting Ollama..."

# Start Ollama in the background
/bin/ollama serve &
OLLAMA_PID=$!

# Wait for Ollama to be ready
echo "Waiting for Ollama to start..."
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
        echo "Ollama is ready!"
        break
    fi
    echo "Waiting... ($i/15)"
    sleep 2
done

# Pull the model if not already present
echo "Checking for model qwen3-vl:2b..."
if ! /bin/ollama list | grep -q "qwen3-vl:2b"; then
    echo "Pulling model qwen3-vl:2b (this may take a few minutes)..."
    /bin/ollama pull qwen3-vl:2b
    echo "Model pulled successfully!"
else
    echo "Model qwen3-vl:2b already present"
fi

echo "Setup complete, keeping Ollama running..."

# Keep Ollama running in foreground
wait $OLLAMA_PID