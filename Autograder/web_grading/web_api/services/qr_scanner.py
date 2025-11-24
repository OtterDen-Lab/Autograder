"""
QR code scanning service for quiz questions.

This service scans QR codes from exam problem images and extracts:
- Question number
- Maximum points
- Encrypted question metadata (passed to QuizGeneration for decryption)
"""
import base64
import json
import logging
from typing import Optional, Dict, List
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


class QRScanner:
  """Service for scanning and processing QR codes from exam problems."""

  def __init__(self):
    """Initialize QR scanner."""
    self.available = PYZBAR_AVAILABLE
    if not PYZBAR_AVAILABLE:
      log.warning("QR scanner unavailable: pyzbar not installed")

  def _preprocess_image_for_qr(self, image: Image.Image) -> List[Image.Image]:
    """
        Apply multiple preprocessing strategies to improve QR detection.

        Args:
            image: PIL Image to preprocess

        Returns:
            List of preprocessed images to try scanning
        """
    from PIL import ImageEnhance, ImageFilter

    variants = []

    # 1. Original image
    variants.append(image.copy())

    # 2. Sharpened image (helps with blurry QR codes)
    sharpened = image.filter(ImageFilter.SHARPEN)
    variants.append(sharpened)

    # 3. High contrast (helps with faded QR codes)
    enhancer = ImageEnhance.Contrast(image)
    high_contrast = enhancer.enhance(2.0)
    variants.append(high_contrast)

    # 4. Grayscale with adaptive threshold simulation (convert to L mode)
    grayscale = image.convert('L')
    # Enhance contrast on grayscale
    enhancer_gray = ImageEnhance.Contrast(grayscale)
    high_contrast_gray = enhancer_gray.enhance(2.5)
    variants.append(high_contrast_gray.convert('RGB'))

    # 5. Sharpened + high contrast combo
    sharpened_contrast = sharpened.copy()
    enhancer_combo = ImageEnhance.Contrast(sharpened_contrast)
    combo = enhancer_combo.enhance(1.5)
    variants.append(combo)

    return variants

  def scan_qr_from_image(self, image_base64: str) -> Optional[Dict]:
    """
        Scan QR code from a base64-encoded image.
        Uses multiple preprocessing strategies to improve detection rate.

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

      # Try multiple preprocessing strategies
      image_variants = self._preprocess_image_for_qr(image)

      for idx, variant in enumerate(image_variants):
        # Scan for QR codes
        qr_codes = pyzbar.decode(variant)

        if qr_codes:
          if idx > 0:
            log.info(f"QR code found using preprocessing strategy #{idx}")

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
            log.warning(
              f"QR code missing required fields (q or pts): {qr_json}")
            continue  # Try next variant

          result = {
            "question_number": question_number,
            "max_points": float(max_points),
            "encrypted_data":
            encrypted_metadata  # Store encrypted string directly
          }

          # Log what we found
          if encrypted_metadata:
            log.info(
              f"Successfully scanned QR code: Q{question_number}, {max_points} pts (has encrypted metadata) : \"{encrypted_metadata}\""
            )
          else:
            log.info(
              f"Successfully scanned QR code: Q{question_number}, {max_points} pts (no metadata)"
            )

          return result

      # No QR codes found in any variant
      log.debug(
        "No QR codes found in image after trying all preprocessing strategies")
      return None

    except Exception as e:
      log.error(f"Error scanning QR code: {e}", exc_info=True)
      return None

  def scan_qr_from_region(self, pdf_base64: str, page_number: int,
                          region_y_start: int,
                          region_y_end: int) -> Optional[Dict]:
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
      problem_page = problem_pdf.new_page(width=region.width,
                                          height=region.height)
      problem_page.show_pdf_page(problem_page.rect,
                                 pdf_document,
                                 page_number,
                                 clip=region)

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

  def scan_multiple_regions(self, pdf_base64: str,
                            regions: List[Dict]) -> Dict[int, Optional[Dict]]:
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

      qr_data = self.scan_qr_from_region(pdf_base64, region["page_number"],
                                         region["region_y_start"],
                                         region["region_y_end"])

      results[problem_number] = qr_data

    return results
