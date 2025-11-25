"""
Server-Sent Events (SSE) infrastructure for real-time updates.

SSE provides one-way communication from server to client, perfect for:
- Background task progress updates
- File upload/processing status
- Finalization progress
"""
import asyncio
import json
from typing import AsyncGenerator, Dict, Any
from datetime import datetime
import logging

log = logging.getLogger(__name__)

# Global registry of active event streams
# Format: {stream_id: asyncio.Queue}
_event_streams: Dict[str, asyncio.Queue] = {}


def create_stream(stream_id: str) -> asyncio.Queue:
  """Create a new event stream.

    Args:
        stream_id: Unique identifier for this stream (e.g., "upload_123")

    Returns:
        asyncio.Queue for sending events
    """
  if stream_id in _event_streams:
    log.warning(f"Stream {stream_id} already exists, returning existing queue")
    return _event_streams[stream_id]

  queue = asyncio.Queue()
  _event_streams[stream_id] = queue
  log.info(f"Created SSE stream: {stream_id}")
  return queue


def get_stream(stream_id: str) -> asyncio.Queue:
  """Get an existing event stream.

    Args:
        stream_id: Stream identifier

    Returns:
        asyncio.Queue or None if not found
    """
  return _event_streams.get(stream_id)


def close_stream(stream_id: str):
  """Close and remove an event stream.

    Args:
        stream_id: Stream identifier
    """
  if stream_id in _event_streams:
    log.info(f"Closing SSE stream: {stream_id}")
    del _event_streams[stream_id]


async def send_event(stream_id: str, event_type: str, data: Dict[str, Any]):
  """Send an event to a stream.

    Args:
        stream_id: Stream identifier
        event_type: Type of event (e.g., "progress", "complete", "error")
        data: Event data dictionary
    """
  queue = get_stream(stream_id)
  if queue:
    await queue.put({
      "event": event_type,
      "data": data,
      "timestamp": datetime.now().isoformat()
    })
    log.info(
      f"Sent {event_type} event to stream {stream_id}: {data.get('message', '')[:50]}"
    )
  else:
    log.warning(f"Attempted to send event to non-existent stream: {stream_id}")


async def event_generator(stream_id: str) -> AsyncGenerator[str, None]:
  """Generate SSE-formatted events for a stream.

    Args:
        stream_id: Stream identifier

    Yields:
        SSE-formatted event strings
    """
  queue = get_stream(stream_id)
  if not queue:
    log.error(f"Stream {stream_id} not found")
    return

  try:
    # Send initial connection event
    yield f"event: connected\ndata: {json.dumps({'stream_id': stream_id})}\n\n"

    while True:
      # Wait for next event with timeout to send keepalive
      try:
        event = await asyncio.wait_for(queue.get(), timeout=30.0)

        # Format as SSE
        event_type = event.get("event", "message")
        data = event.get("data", {})

        # Add timestamp if not present
        if "timestamp" not in data:
          data["timestamp"] = event.get("timestamp",
                                        datetime.now().isoformat())

        yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

        # If this is a completion event, close the stream
        if event_type in ("complete", "error", "cancelled"):
          log.info(f"Stream {stream_id} completed with event: {event_type}")
          break

      except asyncio.TimeoutError:
        # Send keepalive comment
        yield ":keepalive\n\n"

  except asyncio.CancelledError:
    log.info(f"Stream {stream_id} cancelled by client")
  except Exception as e:
    log.error(f"Error in event generator for {stream_id}: {e}")
    yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
  finally:
    close_stream(stream_id)


def make_stream_id(prefix: str, *args) -> str:
  """Create a standardized stream ID.

    Args:
        prefix: Stream type prefix (e.g., "upload", "finalize")
        *args: Additional identifiers (session_id, etc.)

    Returns:
        Stream ID string
    """
  parts = [prefix] + [str(arg) for arg in args]
  return "_".join(parts)
