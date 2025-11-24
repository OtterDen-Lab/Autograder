"""
Manual alignment service - creates composite images and handles user-defined split points
"""
from typing import List, Dict, Optional
from pathlib import Path
import logging
import base64
import numpy as np
from PIL import Image
import io
import fitz

log = logging.getLogger(__name__)


class ManualAlignmentService:
  """Service for manual alignment of exam split points"""

  def create_composite_images(
      self,
      input_files: List[Path],
      output_dir: Optional[Path] = None,
      alpha: float = 0.3) -> tuple[Dict[int, str], Dict[int, tuple[int, int]]]:
    """
        Create composite overlay images for each page number across all exams.

        Args:
            input_files: List of PDF file paths
            output_dir: Optional directory to save composite images (if None, returns base64)
            alpha: Transparency level for each page (0.3 = 30% opacity per page)

        Returns:
            Tuple of (composites, dimensions) where:
            - composites: Dict mapping page_number -> base64 image string (or file path)
            - dimensions: Dict mapping page_number -> (width, height) in pixels
        """
    if not input_files:
      return {}, {}

    log.info(f"Creating composite images from {len(input_files)} exams")

    # Determine max page count across all PDFs
    max_pages = 0
    for pdf_path in input_files:
      try:
        doc = fitz.open(str(pdf_path))
        max_pages = max(max_pages, doc.page_count)
        doc.close()
      except Exception as e:
        log.error(f"Failed to open {pdf_path.name}: {e}")
        continue

    log.info(f"Maximum pages across all exams: {max_pages}")

    # Determine target dimensions by finding the most common page size at 150 DPI
    # This ensures consistent rendering across all PDFs
    target_dimensions = self._get_target_dimensions(input_files)
    if target_dimensions:
      log.info(
        f"Target composite dimensions: {target_dimensions[0]}x{target_dimensions[1]} pixels"
      )

    # Create composite for each page number
    composites = {}
    dimensions = {}  # Track (width, height) for each composite

    for page_num in range(max_pages):
      log.info(f"Creating composite for page {page_num + 1}/{max_pages}")

      page_images = []

      # Collect all images for this page number
      for pdf_path in input_files:
        try:
          doc = fitz.open(str(pdf_path))

          if page_num < doc.page_count:
            page = doc[page_num]

            # Render page to image at consistent DPI
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("png")

            # Convert to PIL Image
            img = Image.open(io.BytesIO(img_bytes))

            # Convert to RGB if needed (remove alpha channel)
            if img.mode != 'RGB':
              img = img.convert('RGB')

            # Resize to target dimensions if different
            if target_dimensions and img.size != target_dimensions:
              log.debug(
                f"Resizing {pdf_path.name} page {page_num} from {img.size} to {target_dimensions}"
              )
              img = img.resize(target_dimensions, Image.Resampling.LANCZOS)

            page_images.append(img)

          doc.close()
        except Exception as e:
          log.error(
            f"Failed to process page {page_num} from {pdf_path.name}: {e}")
          continue

      if not page_images:
        log.warning(f"No images found for page {page_num}")
        continue

      # Create composite by averaging all images
      composite = self._create_overlay_composite(page_images, alpha)

      # Record dimensions (width, height) of this composite
      dimensions[page_num] = composite.size

      # Convert to base64 or save to file
      if output_dir:
        output_path = output_dir / f"composite_page_{page_num + 1}.png"
        composite.save(output_path)
        composites[page_num] = str(output_path)
        log.info(f"Saved composite to {output_path}")
      else:
        # Convert to base64
        buffer = io.BytesIO()
        composite.save(buffer, format='PNG')
        img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        composites[page_num] = img_base64

    log.info(f"Created {len(composites)} composite images")
    return composites, dimensions

  def _get_target_dimensions(
      self, input_files: List[Path]) -> Optional[tuple[int, int]]:
    """
        Determine target dimensions by finding the most common page size at 150 DPI.
        This ensures all PDFs render to the same pixel dimensions regardless of source resolution.

        Args:
            input_files: List of PDF file paths

        Returns:
            Tuple of (width, height) in pixels, or None if no files
        """
    from collections import Counter

    # Collect dimensions of first page from each PDF
    dimensions_list = []
    for pdf_path in input_files:
      try:
        doc = fitz.open(str(pdf_path))
        if doc.page_count > 0:
          page = doc[0]
          pix = page.get_pixmap(dpi=150)
          dimensions_list.append((pix.width, pix.height))
        doc.close()
      except Exception as e:
        log.error(f"Failed to get dimensions from {pdf_path.name}: {e}")
        continue

    if not dimensions_list:
      return None

    # Find most common dimensions
    dimension_counts = Counter(dimensions_list)
    most_common = dimension_counts.most_common(1)[0]
    target_dims, count = most_common

    if count < len(dimensions_list):
      log.info(
        f"Found {len(dimensions_list) - count} PDFs with non-standard dimensions that will be resized"
      )

    return target_dims

  def _create_overlay_composite(self,
                                images: List[Image.Image],
                                alpha: float = 0.3) -> Image.Image:
    """
        Create a composite image by overlaying multiple images with brightness-based transparency.

        Dark pixels (black ink) are rendered opaque, while lighter pixels are very transparent.
        This ensures that handwritten ink remains visible even when stacking many exams.

        Args:
            images: List of PIL Images to overlay
            alpha: Base transparency level (now modulated by pixel brightness)

        Returns:
            Composite PIL Image
        """
    if not images:
      raise ValueError("No images provided")

    # Get dimensions from first image (assume all are same size)
    width, height = images[0].size

    # Resize all images to match first image size (handle any size variations)
    resized_images = []
    for img in images:
      if img.size != (width, height):
        log.warning(f"Resizing image from {img.size} to {width}x{height}")
        img = img.resize((width, height), Image.Resampling.LANCZOS)
      resized_images.append(img)

    # Convert images to numpy arrays
    arrays = [np.array(img, dtype=np.float32) for img in resized_images]

    # Initialize composite with white background
    composite_array = np.ones_like(arrays[0], dtype=np.float32) * 255

    # Brightness-based alpha blending
    for arr in arrays:
      # Calculate brightness for each pixel (average across RGB channels)
      # Shape: (height, width)
      brightness = np.mean(arr, axis=2, keepdims=True)

      # Calculate per-pixel alpha based on darkness
      # Brightness range: 0 (black) to 255 (white)
      # We want: dark pixels (< 25) -> alpha ≈ 1.0 (fully opaque)
      #          medium pixels (25-230) -> alpha decreases from 1.0 to 0.05
      #          light pixels (> 230) -> alpha ≈ 0.05 (very transparent)

      # Normalize brightness to 0-1 range
      normalized_brightness = brightness / 255.0

      # Create threshold-based alpha:
      # - Very dark (< 10% brightness / > 90% dark): alpha = 1.0 (fully opaque)
      # - Moderately dark to light: smooth transition from 1.0 to 0.05
      darkness_threshold = 0.1  # 10% brightness = 90% dark
      light_threshold = 0.9  # 90% brightness = 10% dark

      # Calculate per-pixel alpha
      pixel_alpha = np.where(
        normalized_brightness < darkness_threshold,
        1.0,  # Fully opaque for very dark pixels
        np.where(
          normalized_brightness > light_threshold,
          0.05,  # Very transparent for light pixels
          # Linear interpolation for medium pixels
          1.0 - ((normalized_brightness - darkness_threshold) /
                 (light_threshold - darkness_threshold)) * 0.95))

      # Apply alpha blending with per-pixel alpha
      # composite = composite * (1 - pixel_alpha) + arr * pixel_alpha
      composite_array = composite_array * (1 - pixel_alpha) + arr * pixel_alpha

    # Clip values to valid range and convert back to uint8
    composite_array = np.clip(composite_array, 0, 255).astype(np.uint8)

    # Convert back to PIL Image
    composite = Image.fromarray(composite_array, mode='RGB')

    return composite

  def save_split_points(self, split_points: Dict[int, List[int]],
                        output_path: Path) -> None:
    """
        Save manual split points to JSON file.

        Args:
            split_points: Dict mapping page_number -> list of y-positions
            output_path: Path to save JSON file
        """
    import json

    data = {
      "version": "1.0",
      "split_points": {
        str(k): v
        for k, v in split_points.items()
      }
    }

    with open(output_path, 'w') as f:
      json.dump(data, f, indent=2)

    log.info(f"Saved split points to {output_path}")

  def load_split_points(self, input_path: Path) -> Dict[int, List[int]]:
    """
        Load manual split points from JSON file.

        Args:
            input_path: Path to JSON file

        Returns:
            Dict mapping page_number -> list of y-positions
        """
    import json

    with open(input_path, 'r') as f:
      data = json.load(f)

    # Convert string keys back to integers
    split_points = {int(k): v for k, v in data.get("split_points", {}).items()}

    log.info(
      f"Loaded split points for {len(split_points)} pages from {input_path}")
    return split_points
