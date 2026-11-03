#!/usr/bin/env python3
"""Read the check-in token from a ticket PDF's QR code, as a guide's scanner would.

    pip install pypdfium2==5.14.0 zxing-cpp==3.1.1 pillow
    python scripts/read_ticket_qr.py ticket.pdf          # prints the token (a JWS)

Renders the first page at 200 dpi and decodes the QR code on it. For the smoke test (docs/smoke-test.md, section 10):
the token then goes to POST /api/check-ins, exactly as a scanner app would send it.
"""

from __future__ import annotations

import argparse
import sys

import pypdfium2
import zxingcpp


def token_from_pdf(path: str) -> str:
    page = pypdfium2.PdfDocument(path)[0]
    image = page.render(scale=200 / 72).to_pil()
    codes = [code for code in zxingcpp.read_barcodes(image) if code.format == zxingcpp.BarcodeFormat.QRCode]
    if not codes:
        raise SystemExit(f"no QR code found in {path}")
    return codes[0].text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf")
    args = parser.parse_args()
    sys.stdout.write(token_from_pdf(args.pdf) + "\n")


if __name__ == "__main__":
    main()
