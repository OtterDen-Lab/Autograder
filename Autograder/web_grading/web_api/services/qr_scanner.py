"""
QR code scanning and decryption service for quiz questions.

This service scans QR codes from exam problem images and extracts:
- Question number
- Maximum points
- Encrypted question metadata (type, seed, version)
"""
import base64
import json
import logging
import os
import sys
from typing import Optional, Dict, List
from pathlib import Path
from PIL import Image
import io

log = logging.getLogger(__name__)

# Try to import pyzbar for QR code scanning
try:
    from pyzbar import pyzbar
    PYZBAR_AVAILABLE = True
except ImportError:
    log.warning("pyzbar not installed - QR code scanning will not be available")
    PYZBAR_AVAILABLE = False

# Try to import cryptography for decryption
try:
    from cryptography.fernet import Fernet
    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    log.warning("cryptography not installed - QR code decryption will not be available")
    CRYPTOGRAPHY_AVAILABLE = False
    Fernet = None


# Minimal decryption implementation (doesn't require segno dependency)
class MinimalQuestionQRCode:
    """Minimal implementation of QR code decryption without requiring full QuizGeneration module."""

    @classmethod
    def get_encryption_key(cls) -> bytes:
        """Get encryption key from environment."""
        if not CRYPTOGRAPHY_AVAILABLE:
            return b''

        key_str = os.environ.get('QUIZ_ENCRYPTION_KEY')
        if key_str is None:
            log.warning("QUIZ_ENCRYPTION_KEY not set! Using temporary key (insecure)")
            return Fernet.generate_key()
        return key_str.encode()

    @classmethod
    def decrypt_question_data(cls, encrypted_data: str, key: bytes = None) -> Dict:
        """Decode question regeneration data from QR code."""
        if key is None:
            key = cls.get_encryption_key()

        try:
            # Decode from base64
            obfuscated = base64.urlsafe_b64decode(encrypted_data.encode())

            # Reverse XOR obfuscation
            if key:
                key_bytes = key[:16] if isinstance(key, bytes) else key.encode()[:16]
                data_bytes = bytes(b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(obfuscated))
            else:
                data_bytes = obfuscated

            data_str = data_bytes.decode('utf-8')
            
            log.debug(data_str)

            # Parse data string (format: "question_type:seed:version")
            parts = data_str.split(':')
            if len(parts) != 3:
                raise ValueError(f"Invalid encoded data format: expected 3 parts, got {len(parts)}")

            question_type, seed_str, version = parts

            return {
                "question_type": question_type,
                "seed": int(seed_str),
                "version": version
            }
        except Exception as e:
            log.error(f"Failed to decode question data: {e}")
            raise ValueError(f"Failed to decode QR code data: {e}")


# Use the minimal implementation (avoids importing segno)
QuestionQRCode = MinimalQuestionQRCode
QUIZ_GENERATOR_AVAILABLE = CRYPTOGRAPHY_AVAILABLE


class QRScanner:
    """Service for scanning and processing QR codes from exam problems."""

    def __init__(self):
        """Initialize QR scanner."""
        self.available = PYZBAR_AVAILABLE and QUIZ_GENERATOR_AVAILABLE
        if not PYZBAR_AVAILABLE:
            log.warning("QR scanner unavailable: pyzbar not installed")
        if not QUIZ_GENERATOR_AVAILABLE:
            log.warning("QR scanner unavailable: QuizGeneration module not found")

    def scan_qr_from_image(self, image_base64: str) -> Optional[Dict]:
        """
        Scan QR code from a base64-encoded image.

        Args:
            image_base64: Base64 encoded PNG/JPEG image

        Returns:
            Dict with QR code data if found, None otherwise.
            Format: {
                "question_number": int,
                "max_points": float,
                "question_type": str,
                "seed": int,
                "version": str
            }
        """
        if not self.available:
            log.debug("QR scanner not available, skipping scan")
            return None

        try:
            # Decode image
            image_bytes = base64.b64decode(image_base64)
            image = Image.open(io.BytesIO(image_bytes))

            # Convert to RGB if needed (pyzbar works best with RGB)
            if image.mode != 'RGB':
                image = image.convert('RGB')

            # Scan for QR codes
            qr_codes = pyzbar.decode(image)

            if not qr_codes:
                log.debug("No QR codes found in image")
                return None

            # Process first QR code found
            qr_data = qr_codes[0].data.decode('utf-8')
            log.debug(f"Found QR code data: {qr_data[:100]}...")

            # Parse JSON from QR code
            qr_json = json.loads(qr_data)

            # Extract basic fields
            question_number = qr_json.get('q')
            max_points = qr_json.get('pts')
            encrypted_metadata = qr_json.get('s')

            # At minimum we need question number and points
            if question_number is None or max_points is None:
                log.warning(f"QR code missing required fields (q or pts): {qr_json}")
                return None

            result = {
                "question_number": question_number,
                "max_points": float(max_points),
                "encrypted_data": encrypted_metadata  # Store encrypted string directly
            }

            # Log what we found
            if encrypted_metadata:
                log.info(f"Successfully scanned QR code: Q{question_number}, {max_points} pts (has encrypted metadata)")
            else:
                log.info(f"Successfully scanned QR code: Q{question_number}, {max_points} pts (no metadata)")

            return result

        except Exception as e:
            log.error(f"Error scanning QR code: {e}", exc_info=True)
            return None

    def decrypt_metadata(self, encrypted_str: str) -> Optional[Dict]:
        """
        Decrypt the encrypted metadata from QR code.

        Args:
            encrypted_str: Base64 encoded encrypted string

        Returns:
            Dict with decrypted metadata:
            {
                "question_type": str,
                "seed": int,
                "version": str
            }
        """
        if not QUIZ_GENERATOR_AVAILABLE:
            log.error("Cannot decrypt: QuizGeneration module not available")
            return None

        try:
            metadata = QuestionQRCode.decrypt_question_data(encrypted_str)
            return metadata
        except Exception as e:
            log.error(f"Error decrypting QR metadata: {e}")
            return None

    def scan_qr_from_region(
        self,
        pdf_base64: str,
        page_number: int,
        region_y_start: int,
        region_y_end: int
    ) -> Optional[Dict]:
        """
        Extract a region from a PDF and scan for QR codes.

        Args:
            pdf_base64: Base64 encoded PDF
            page_number: 0-indexed page number
            region_y_start: Y coordinate of region start
            region_y_end: Y coordinate of region end

        Returns:
            Dict with QR code data if found, None otherwise
        """
        if not self.available:
            return None

        try:
            import fitz  # PyMuPDF

            # Decode PDF
            pdf_bytes = base64.b64decode(pdf_base64)
            pdf_document = fitz.open("pdf", pdf_bytes)

            # Get page
            page = pdf_document[page_number]

            # Create region rectangle
            region = fitz.Rect(0, region_y_start, page.rect.width, region_y_end)

            # Extract region as image
            problem_pdf = fitz.open()
            problem_page = problem_pdf.new_page(width=region.width, height=region.height)
            problem_page.show_pdf_page(problem_page.rect, pdf_document, page_number, clip=region)

            # Convert to PNG
            pix = problem_page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")
            img_base64 = base64.b64encode(img_bytes).decode("utf-8")

            # Cleanup
            problem_pdf.close()
            pdf_document.close()

            # Scan QR code from extracted image
            return self.scan_qr_from_image(img_base64)

        except Exception as e:
            log.error(f"Error scanning QR from PDF region: {e}", exc_info=True)
            return None

    def scan_multiple_regions(
        self,
        pdf_base64: str,
        regions: List[Dict]
    ) -> Dict[int, Optional[Dict]]:
        """
        Scan multiple regions for QR codes.

        Args:
            pdf_base64: Base64 encoded PDF
            regions: List of region dicts with keys:
                     - page_number
                     - region_y_start
                     - region_y_end
                     - problem_number

        Returns:
            Dict mapping problem_number -> QR data (or None if not found)
        """
        results = {}

        for region in regions:
            problem_number = region.get("problem_number")
            if not problem_number:
                continue

            qr_data = self.scan_qr_from_region(
                pdf_base64,
                region["page_number"],
                region["region_y_start"],
                region["region_y_end"]
            )

            results[problem_number] = qr_data

        return results