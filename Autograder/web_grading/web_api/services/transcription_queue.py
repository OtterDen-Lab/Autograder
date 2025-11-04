"""
Background transcription queue for processing handwriting with Ollama.

This module provides a priority queue system for transcribing handwriting in the background.
Problems are processed by priority (high priority first), and results are cached in the database.
"""

import logging
import queue
import threading
import time
import json
import base64
import io
from typing import Optional, Dict
from datetime import datetime
import textwrap
from PIL import Image

from ..database import get_db_connection
from Autograder.ai_helper import AI_Helper__Ollama
from ..routes.problems import extract_problem_image

log = logging.getLogger(__name__)


class TranscriptionQueue:
    """
    Singleton queue for managing background handwriting transcription tasks.

    Uses a priority queue where lower numbers = higher priority.
    Priority levels:
    - 0: User-requested (clicked "Decipher handwriting")
    - 1: Currently visible problem
    - 2: Next few problems (prefetch)
    - 3: Background batch processing
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self.task_queue = queue.PriorityQueue()
        self.processing = {}  # {problem_id: timestamp}
        self.worker_thread = None
        self.running = False
        self.ai_helper = None

        log.info("TranscriptionQueue initialized")

    def start(self):
        """Start the background worker thread."""
        if self.running:
            log.warning("TranscriptionQueue worker already running")
            return

        self.running = True
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()
        log.info("TranscriptionQueue worker thread started")

    def stop(self):
        """Stop the background worker thread."""
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=5)
        log.info("TranscriptionQueue worker thread stopped")

    def add_task(self, problem_id: int, priority: int = 3):
        """
        Add a transcription task to the queue.

        Args:
            problem_id: ID of the problem to transcribe
            priority: Priority level (0=highest, 3=lowest)
        """
        # Check if already processed
        if self._is_cached(problem_id):
            log.debug(f"Problem {problem_id} already has cached transcription, skipping")
            return

        # Check if already in queue or processing
        if problem_id in self.processing:
            log.debug(f"Problem {problem_id} already being processed")
            return

        # Add to queue with priority
        self.task_queue.put((priority, time.time(), problem_id))
        log.debug(f"Added problem {problem_id} to transcription queue with priority {priority}")

    def bump_priority(self, problem_id: int, new_priority: int = 0):
        """
        Bump a problem to higher priority by re-adding it with higher priority.

        Args:
            problem_id: ID of the problem to prioritize
            new_priority: New priority level (default 0 = highest)
        """
        # If already cached, no need to bump
        if self._is_cached(problem_id):
            log.debug(f"Problem {problem_id} already cached, no need to bump priority")
            return

        # If currently processing, can't bump (but it will finish soon)
        if problem_id in self.processing:
            log.debug(f"Problem {problem_id} already processing, cannot bump")
            return

        # Add with high priority (will be processed before lower priority items)
        self.task_queue.put((new_priority, time.time(), problem_id))
        log.info(f"Bumped problem {problem_id} to priority {new_priority}")

    def get_status(self, problem_id: int) -> Dict:
        """
        Get the transcription status for a problem.

        Returns:
            dict with keys: status ("cached", "processing", "queued", "not_started"),
                           transcription (if cached), model (if cached)
        """
        # Check cache first
        cached = self._get_cached(problem_id)
        if cached:
            return {
                "status": "cached",
                "transcription": cached["transcription"],
                "model": cached["model"]
            }

        # Check if processing
        if problem_id in self.processing:
            return {"status": "processing"}

        # Check if in queue (this is expensive, so only do if needed)
        # For now, just return "not_started" if not cached or processing
        return {"status": "not_started"}

    def _is_cached(self, problem_id: int) -> bool:
        """Check if transcription is already cached in database."""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT transcription FROM problems
                    WHERE id = ? AND transcription IS NOT NULL
                """, (problem_id,))
                return cursor.fetchone() is not None
        except Exception as e:
            log.error(f"Error checking cache for problem {problem_id}: {e}")
            return False

    def _get_cached(self, problem_id: int) -> Optional[Dict]:
        """Get cached transcription from database."""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT transcription, transcription_model FROM problems
                    WHERE id = ?
                """, (problem_id,))
                row = cursor.fetchone()
                if row and row[0]:
                    return {
                        "transcription": row[0],
                        "model": row[1] or "Ollama (background)"
                    }
        except Exception as e:
            log.error(f"Error getting cached transcription for problem {problem_id}: {e}")
        return None

    def _save_to_cache(self, problem_id: int, transcription: str, model: str):
        """Save transcription to database cache."""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE problems
                    SET transcription = ?, transcription_model = ?, transcription_cached_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (transcription, model, problem_id))
                conn.commit()
                log.debug(f"Cached transcription for problem {problem_id}")
        except Exception as e:
            log.error(f"Error saving transcription to cache for problem {problem_id}: {e}")

    def _worker(self):
        """Background worker that processes transcription tasks."""
        log.info("TranscriptionQueue worker starting")

        # Initialize AI helper lazily
        if self.ai_helper is None:
            try:
                self.ai_helper = AI_Helper__Ollama()
                log.info("Initialized Ollama AI helper for transcription queue")
            except Exception as e:
                log.error(f"Failed to initialize Ollama AI helper: {e}")
                return

        while self.running:
            try:
                # Get next task (blocks with timeout)
                try:
                    priority, timestamp, problem_id = self.task_queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                # Mark as processing
                self.processing[problem_id] = datetime.now()

                log.info(f"Processing transcription for problem {problem_id} (priority {priority})")

                # Get problem image from database
                image_base64 = self._get_problem_image(problem_id)
                if not image_base64:
                    log.error(f"Could not get image for problem {problem_id}")
                    del self.processing[problem_id]
                    continue

                # Log image details for debugging
                image_size_kb = len(image_base64) / 1024
                log.info(f"Problem {problem_id}: Image size = {image_size_kb:.1f} KB (base64)")
                log.debug(f"Problem {problem_id}: Base64 data (first 100 chars): {image_base64[:100]}...")
                log.debug(f"Problem {problem_id}: Full base64 length: {len(image_base64)}")

                # Compress image for Ollama (JPEG, lower quality) to reduce timeout issues
                compressed_image = self._compress_image_for_ollama(image_base64)
                compressed_size_kb = len(compressed_image) / 1024
                log.info(f"Problem {problem_id}: Compressed to {compressed_size_kb:.1f} KB (was {image_size_kb:.1f} KB)")

                # Optionally save to file for curl testing (set DEBUG_SAVE_IMAGES=1 in env)
                import os
                if os.getenv("DEBUG_SAVE_IMAGES") == "1":
                    debug_file = f"/tmp/problem_{problem_id}_image.base64"
                    with open(debug_file, "w") as f:
                        f.write(compressed_image)
                    log.info(f"Problem {problem_id}: Saved compressed base64 to {debug_file}")

                # Transcribe with Ollama
                query = textwrap.dedent("""
                    Please transcribe all handwritten text from this exam answer with maximum accuracy.

                    Instructions:
                    - Transcribe ONLY handwritten text (ignore printed questions/instructions)
                    - Preserve the structure and organization of the answer exactly
                    - For unclear text, make your best interpretation and note uncertainty with [possibly: "alternative"]
                    - Describe any diagrams, drawings, or mathematical figures in detail within [brackets]
                    - Maintain all mathematical notation, equations, and symbols precisely
                    - Note any corrections, cross-outs, or marginal notes

                    Respond with just the transcribed text, being as thorough and accurate as possible.
                """)

                try:
                    transcription, usage_info = self.ai_helper.query_ai(
                        query,
                        attachments=[("jpeg", compressed_image)]
                    )

                    # Save to cache
                    model_name = f"Ollama (background)"
                    self._save_to_cache(problem_id, transcription, model_name)

                    log.info(f"Successfully transcribed problem {problem_id}")

                except Exception as e:
                    log.error(f"Error transcribing problem {problem_id}: {e}")

                # Remove from processing
                del self.processing[problem_id]

                # Mark task as done
                self.task_queue.task_done()

            except Exception as e:
                log.error(f"Error in transcription worker: {e}", exc_info=True)
                time.sleep(1)  # Avoid tight loop on repeated errors

        log.info("TranscriptionQueue worker stopped")

    def _compress_image_for_ollama(self, image_base64: str) -> str:
        """
        Compress and resize image to JPEG for faster Ollama processing.
        Reduces to 50% size and JPEG quality=50.

        Args:
            image_base64: Base64-encoded PNG image

        Returns:
            Base64-encoded JPEG image (compressed and resized)
        """
        try:
            # Decode base64 to image
            img_bytes = base64.b64decode(image_base64)
            img = Image.open(io.BytesIO(img_bytes))

            # Resize to 50% (equivalent to reducing DPI from 150 to 75)
            new_size = (img.width // 4, img.height // 4)
            img = img.resize(new_size, Image.Resampling.LANCZOS)

            # Convert RGBA to RGB if needed (JPEG doesn't support transparency)
            if img.mode in ('RGBA', 'LA', 'P'):
                # Create white background
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            # Compress to JPEG with quality=50 (lower quality for smaller size)
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=50, optimize=True)
            compressed_bytes = buffer.getvalue()

            # Encode back to base64
            return base64.b64encode(compressed_bytes).decode('utf-8')
        except Exception as e:
            log.error(f"Error compressing image: {e}")
            # Return original if compression fails
            return image_base64

    def _get_problem_image(self, problem_id: int) -> Optional[str]:
        """Get base64-encoded image data for a problem by extracting from PDF."""
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()

                # Get problem metadata and submission info
                cursor.execute("""
                    SELECT region_coords, submission_id FROM problems WHERE id = ?
                """, (problem_id,))
                row = cursor.fetchone()
                if not row or not row[0]:
                    log.error(f"No region_coords found for problem {problem_id}")
                    return None

                region_data = json.loads(row[0])
                submission_id = row[1]

                # Get PDF data from submission
                cursor.execute("""
                    SELECT exam_pdf_data FROM submissions WHERE id = ?
                """, (submission_id,))
                submission_row = cursor.fetchone()

                if not submission_row or not submission_row[0]:
                    log.error(f"No PDF data found for submission {submission_id}")
                    return None

                # Extract image from PDF using shared function
                return extract_problem_image(
                    submission_row[0],
                    region_data["page_number"],
                    region_data["region_y_start"],
                    region_data["region_y_end"],
                    region_data.get("end_page_number"),
                    region_data.get("end_region_y")
                )
        except Exception as e:
            log.error(f"Error getting image for problem {problem_id}: {e}", exc_info=True)
        return None


# Global instance
_queue_instance = None

def get_transcription_queue() -> TranscriptionQueue:
    """Get the global transcription queue instance."""
    global _queue_instance
    if _queue_instance is None:
        _queue_instance = TranscriptionQueue()
    return _queue_instance