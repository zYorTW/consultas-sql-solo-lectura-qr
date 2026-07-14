"""Generación de la imagen QR a partir del payload JSON del resultado."""
import qrcode


def generate_qr_image(text_data):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(text_data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")
