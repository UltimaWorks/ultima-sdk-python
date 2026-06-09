"""Ultima Online Asset Viewer Webapp."""

import argparse
import io
import logging

from flask import Flask, jsonify, render_template_string, request, send_file
from PIL import Image

from ultima_sdk_python import UltimaSDK
from ultima_sdk_python.utils import add_uo_root_arg, resolve_uo_root

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

sdk = None


HTML_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Ultima Online Asset Viewer</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
            background: #f5f5f5;
        }
        h1 {
            margin-bottom: 8px;
        }
        .muted {
            color: #666;
            margin-bottom: 20px;
        }
        .section {
            background: white;
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 16px;
            margin-bottom: 16px;
        }
        form {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            align-items: center;
        }
        input, select, button {
            padding: 8px 10px;
            font-size: 14px;
        }
        button {
            cursor: pointer;
        }
        .preview {
            margin-top: 16px;
        }
        img {
            max-width: 100%;
            border: 1px solid #ccc;
            background: #fff;
        }
        code {
            background: #eee;
            padding: 2px 4px;
            border-radius: 4px;
        }
    </style>
</head>
<body>
    <h1>Ultima Online Asset Viewer</h1>
    <div class="muted">Browse and render assets from your Ultima Online client files.</div>

    <div class="section">
        <h2>Land Tile</h2>
        <form action="/view/land" method="get">
            <label for="land_id">Tile ID</label>
            <input id="land_id" name="id" type="number" min="0" required>
            <button type="submit">View</button>
        </form>
        <p>Direct image URL: <code>/api/image/land/&lt;id&gt;</code></p>
    </div>

    <div class="section">
        <h2>Static Art</h2>
        <form action="/view/static" method="get">
            <label for="static_id">Art ID</label>
            <input id="static_id" name="id" type="number" min="0" required>
            <button type="submit">View</button>
        </form>
        <p>Direct image URL: <code>/api/image/static/&lt;id&gt;</code></p>
    </div>

    <div class="section">
        <h2>Gump</h2>
        <form action="/view/gump" method="get">
            <label for="gump_id">Gump ID</label>
            <input id="gump_id" name="id" type="number" min="0" required>
            <button type="submit">View</button>
        </form>
        <p>Direct image URL: <code>/api/image/gump/&lt;id&gt;</code></p>
    </div>
</body>
</html>
"""


VIEW_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{{ asset_type|title }} {{ asset_id }}</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 20px;
            background: #f5f5f5;
        }
        .card {
            background: white;
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 16px;
            max-width: 900px;
        }
        img {
            max-width: 100%;
            border: 1px solid #ccc;
            background: #fff;
        }
        a {
            text-decoration: none;
        }
    </style>
</head>
<body>
    <div class="card">
        <p><a href="/">← Back</a></p>
        <h1>{{ asset_type|title }} {{ asset_id }}</h1>
        <img src="/api/image/{{ asset_type }}/{{ asset_id }}" alt="{{ asset_type }} {{ asset_id }}">
    </div>
</body>
</html>
"""


def initialize_assets(uo_root: str) -> bool:
    global sdk
    try:
        sdk = UltimaSDK(uo_root)
        return True
    except Exception as e:
        app.logger.exception(e)
        return False


def pil_image_to_png_response(image: Image.Image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return send_file(buffer, mimetype="image/png")


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/health")
def health():
    try:
        return jsonify({
            "status": "ok",
            "initialized": sdk is not None
        })
    except Exception as e:
        app.logger.exception(e)
        return jsonify({"error": "An internal error occurred"}), 500


@app.route("/api/info/land/<int:asset_id>")
def land_info(asset_id: int):
    try:
        if sdk is None:
            return jsonify({"error": "Assets not initialized"}), 503

        info = sdk.land.get_tile_data(asset_id)
        return jsonify({
            "id": asset_id,
            "name": getattr(info, "name", None),
            "flags": getattr(info, "flags", None),
            "texture_id": getattr(info, "texture_id", None),
        })
    except Exception as e:
        app.logger.exception(e)
        return jsonify({"error": "An internal error occurred"}), 500


@app.route("/api/info/static/<int:asset_id>")
def static_info(asset_id: int):
    try:
        if sdk is None:
            return jsonify({"error": "Assets not initialized"}), 503

        info = sdk.art.get_tile_data(asset_id)
        return jsonify({
            "id": asset_id,
            "name": getattr(info, "name", None),
            "flags": getattr(info, "flags", None),
            "height": getattr(info, "height", None),
            "weight": getattr(info, "weight", None),
        })
    except Exception as e:
        app.logger.exception(e)
        return jsonify({"error": "An internal error occurred"}), 500


@app.route("/api/info/gump/<int:asset_id>")
def gump_info(asset_id: int):
    try:
        if sdk is None:
            return jsonify({"error": "Assets not initialized"}), 503

        return jsonify({
            "id": asset_id,
            "exists": sdk.gumps.exists(asset_id)
        })
    except Exception as e:
        app.logger.exception(e)
        return jsonify({"error": "An internal error occurred"}), 500


@app.route("/api/image/land/<int:asset_id>")
def land_image(asset_id: int):
    try:
        if sdk is None:
            return jsonify({"error": "Assets not initialized"}), 503

        image = sdk.land.render(asset_id)
        if image is None:
            return jsonify({"error": "Asset not found"}), 404

        return pil_image_to_png_response(image)
    except Exception as e:
        app.logger.exception(e)
        return jsonify({"error": "An internal error occurred"}), 500


@app.route("/api/image/static/<int:asset_id>")
def static_image(asset_id: int):
    try:
        if sdk is None:
            return jsonify({"error": "Assets not initialized"}), 503

        image = sdk.art.render(asset_id)
        if image is None:
            return jsonify({"error": "Asset not found"}), 404

        return pil_image_to_png_response(image)
    except Exception as e:
        app.logger.exception(e)
        return jsonify({"error": "An internal error occurred"}), 500


@app.route("/api/image/gump/<int:asset_id>")
def gump_image(asset_id: int):
    try:
        if sdk is None:
            return jsonify({"error": "Assets not initialized"}), 503

        image = sdk.gumps.render(asset_id)
        if image is None:
            return jsonify({"error": "Asset not found"}), 404

        return pil_image_to_png_response(image)
    except Exception as e:
        app.logger.exception(e)
        return jsonify({"error": "An internal error occurred"}), 500


@app.route("/view/land")
def view_land():
    try:
        asset_id = int(request.args.get("id", "0"))
        return render_template_string(VIEW_TEMPLATE, asset_type="land", asset_id=asset_id)
    except Exception as e:
        app.logger.exception(e)
        return jsonify({"error": "An internal error occurred"}), 500


@app.route("/view/static")
def view_static():
    try:
        asset_id = int(request.args.get("id", "0"))
        return render_template_string(VIEW_TEMPLATE, asset_type="static", asset_id=asset_id)
    except Exception as e:
        app.logger.exception(e)
        return jsonify({"error": "An internal error occurred"}), 500


@app.route("/view/gump")
def view_gump():
    try:
        asset_id = int(request.args.get("id", "0"))
        return render_template_string(VIEW_TEMPLATE, asset_type="gump", asset_id=asset_id)
    except Exception as e:
        app.logger.exception(e)
        return jsonify({"error": "An internal error occurred"}), 500


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    add_uo_root_arg(parser)
    parser.add_argument(
        '--host',
        default='127.0.0.1',
        help='Host to bind to (default: 127.0.0.1)'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=5000,
        help='Port to bind to (default: 5000)'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug mode'
    )

    args = parser.parse_args()

    uo_root = resolve_uo_root(args.uo_root)

    print("Initializing Ultima Online Asset Viewer...")
    print(f"UO Root: {uo_root}")

    if not initialize_assets(uo_root):
        print("Failed to initialize assets. Some features may not work.")
    else:
        print("Asset initialization complete!")

    print(f"Starting web server on http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == '__main__':
    main()
