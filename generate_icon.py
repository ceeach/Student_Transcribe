"""Generate application icon for NotionTranscribe.

Creates multi-resolution icons (.ico and .png) with vibrant contrast for Windows taskbars.
"""

import os
from PIL import Image, ImageDraw


def create_vibrant_icon():
    os.makedirs("assets", exist_ok=True)
    sizes = [256, 128, 64, 48, 32, 24, 16]
    images = []

    for size in sizes:
        # High resolution canvas for antialiasing
        render_size = size * 4 if size <= 64 else size
        img = Image.new("RGBA", (render_size, render_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        pad = int(render_size * 0.05)
        radius = int(render_size * 0.22)

        # Draw vibrant gradient background (electric cyan to rich deep blue)
        # Using vertical gradient simulation
        base_box = [pad, pad, render_size - pad, render_size - pad]
        draw.rounded_rectangle(
            base_box,
            radius=radius,
            fill=(14, 165, 233, 255),  # Electric cyan #0ea5e9
            outline=(255, 255, 255, 180),
            width=max(1, int(render_size * 0.025)),
        )

        # Inner subtle gradient / contrast depth
        inner_pad = pad + int(render_size * 0.04)
        draw.rounded_rectangle(
            [inner_pad, inner_pad + int(render_size * 0.25), render_size - inner_pad, render_size - inner_pad],
            radius=max(1, radius - int(render_size * 0.05)),
            fill=(3, 105, 161, 220),  # Deep ocean blue #0369a1
        )

        # Microphone body (pure white #ffffff for maximum taskbar contrast)
        cx = render_size // 2
        cy = int(render_size * 0.42)
        mw = int(render_size * 0.13)
        mh = int(render_size * 0.26)

        draw.rounded_rectangle(
            [cx - mw, cy - mh // 2, cx + mw, cy + mh // 2],
            radius=mw,
            fill=(255, 255, 255, 255),
        )

        # Microphone cradle arc
        arc_pad = int(render_size * 0.07)
        arc_w = max(2, int(render_size * 0.05))
        draw.arc(
            [cx - mw - arc_pad, cy - mh // 4, cx + mw + arc_pad, cy + mh // 2 + arc_pad],
            start=0,
            end=180,
            fill=(255, 255, 255, 255),
            width=arc_w,
        )

        # Stand stem
        stem_top = cy + mh // 2 + arc_pad
        stem_bot = int(render_size * 0.76)
        draw.line(
            [(cx, stem_top), (cx, stem_bot)],
            fill=(255, 255, 255, 255),
            width=arc_w,
        )

        # Stand horizontal base
        base_w = int(render_size * 0.18)
        draw.line(
            [(cx - base_w, stem_bot), (cx + base_w, stem_bot)],
            fill=(255, 255, 255, 255),
            width=max(2, int(render_size * 0.055)),
        )

        # Downscale smoothly with Lanczos for crystal-clear antialiasing
        if render_size != size:
            final_img = img.resize((size, size), Image.Resampling.LANCZOS)
        else:
            final_img = img

        images.append(final_img)

    # Save PNG (256x256)
    png_path = os.path.join("assets", "icon.png")
    images[0].save(png_path, format="PNG")

    # Save multi-size ICO
    ico_path = os.path.join("assets", "icon.ico")
    # Save the 256x256 image with all sizes included
    images[0].save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[1:],
    )
    print(f"Generated {ico_path} and {png_path} with sizes: {sizes}")


if __name__ == "__main__":
    create_vibrant_icon()
