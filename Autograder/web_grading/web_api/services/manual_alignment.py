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
        alpha: float = 0.3
    ) -> Dict[int, str]:
        """
        Create composite overlay images for each page number across all exams.

        Args:
            input_files: List of PDF file paths
            output_dir: Optional directory to save composite images (if None, returns base64)
            alpha: Transparency level for each page (0.3 = 30% opacity per page)

        Returns:
            Dict mapping page_number -> base64 image string (or file path if output_dir specified)
        """
        if not input_files:
            return {}

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

        # Create composite for each page number
        composites = {}

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

                        page_images.append(img)

                    doc.close()
                except Exception as e:
                    log.error(f"Failed to process page {page_num} from {pdf_path.name}: {e}")
                    continue

            if not page_images:
                log.warning(f"No images found for page {page_num}")
                continue

            # Create composite by averaging all images
            composite = self._create_overlay_composite(page_images, alpha)

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
        return composites

    def _create_overlay_composite(
        self,
        images: List[Image.Image],
        alpha: float = 0.3
    ) -> Image.Image:
        """
        Create a composite image by overlaying multiple images with transparency.

        The overlay approach makes aligned content (like printed text and lines)
        appear darker/more prominent, while misaligned content fades.

        Args:
            images: List of PIL Images to overlay
            alpha: Transparency level per image (lower = more transparent)

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

        # Calculate weighted average
        # Using alpha blending: each image contributes based on alpha value
        composite_array = np.zeros_like(arrays[0], dtype=np.float32)

        for arr in arrays:
            # Blend each image into the composite
            # This creates the "overlay" effect where aligned content is emphasized
            composite_array = composite_array * (1 - alpha) + arr * alpha

        # Clip values to valid range and convert back to uint8
        composite_array = np.clip(composite_array, 0, 255).astype(np.uint8)

        # Convert back to PIL Image
        composite = Image.fromarray(composite_array, mode='RGB')

        return composite

    def save_split_points(
        self,
        split_points: Dict[int, List[int]],
        output_path: Path
    ) -> None:
        """
        Save manual split points to JSON file.

        Args:
            split_points: Dict mapping page_number -> list of y-positions
            output_path: Path to save JSON file
        """
        import json

        data = {
            "version": "1.0",
            "split_points": {str(k): v for k, v in split_points.items()}
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

        log.info(f"Loaded split points for {len(split_points)} pages from {input_path}")
        return split_points
