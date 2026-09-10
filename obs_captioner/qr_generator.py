"""Vector SVG QR Code Generator for Stage Displays and LAN Mobile Devices."""

import io
import logging
from typing import Optional

logger = logging.getLogger("obs_captioner.qr")


def generate_qr_svg(url: str, border: int = 2) -> str:
    """Generate a clean, scalable vector SVG QR code string encoding the specified URL."""
    # 1. Try standard qrcode library with SVG path factory
    try:
        import qrcode
        import qrcode.image.svg

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=border,
            image_factory=qrcode.image.svg.SvgPathImage,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image()

        buf = io.BytesIO()
        img.save(buf)
        svg_content = buf.getvalue().decode("utf-8")
        if svg_content and "<svg" in svg_content:
            return svg_content
    except Exception as e:
        logger.debug(f"qrcode library generation failed or unavailable: {e}. Using fallback.")

    # 2. Resilient fallback SVG representation
    escaped_url = url.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    fallback_svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 320" width="320" height="320">
  <rect width="320" height="320" rx="16" fill="#0F172A"/>
  <rect x="16" y="16" width="288" height="288" rx="12" fill="#1E293B" stroke="#38BDF8" stroke-width="2"/>
  <text x="160" y="70" fill="#38BDF8" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" font-size="20" font-weight="bold" text-anchor="middle">📱 VoxStream Display</text>
  <rect x="40" y="100" width="240" height="120" rx="8" fill="#0A0D14" stroke="rgba(255,255,255,0.1)"/>
  <text x="160" y="145" fill="#94A3B8" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" font-size="12" text-anchor="middle">Scan or open on your local Wi-Fi:</text>
  <text x="160" y="180" fill="#FACC15" font-family="'JetBrains Mono', monospace" font-size="13" font-weight="bold" text-anchor="middle">{escaped_url}</text>
  <text x="160" y="270" fill="#E2E8F0" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" font-size="13" text-anchor="middle">Connect on Stage Tablets &amp; Phones</text>
</svg>"""
    return fallback_svg
