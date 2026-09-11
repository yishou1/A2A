"""Flask application factory for the AMOS simulation platform."""

from __future__ import annotations

import argparse
import os


from flask import Flask, abort, render_template, request

from amos_platform.api.blueprint import create_api_blueprint
from amos_platform.api.dependencies import get_engine
from amos_platform.config import static_dir, template_dir
from amos_platform.frontend_state.temporal_story import media_is_released


DEFAULT_ALGOLIB_CONSOLE_URL = "http://127.0.0.1:5173/algorithms"


def create_app() -> Flask:
    """Create the Flask app with repository template and static paths."""
    app = Flask(
        __name__,
        template_folder=str(template_dir()),
        static_folder=str(static_dir()),
    )
    app.config["ALGOLIB_CONSOLE_URL"] = (
        os.getenv("ALGOLIB_CONSOLE_URL", "").strip() or DEFAULT_ALGOLIB_CONSOLE_URL
    )
    app.register_blueprint(create_api_blueprint())

    @app.before_request
    def enforce_scripted_media_release():
        """Prevent guessed future media URLs from bypassing causal projection."""
        prefix = "/static/assets/scenarios/"
        if not request.path.startswith(prefix):
            return None
        engine = get_engine()
        story = getattr(engine, "scenario_story", {}) or {}
        if not media_is_released(
            story,
            request.path,
            float(engine.clock.get("elapsed_sec", 0) or 0),
        ):
            abort(404)
        return None

    @app.route("/")
    def index():
        return render_template(
            "dashboard.html",
            algolib_console_url=app.config["ALGOLIB_CONSOLE_URL"],
        )

    return app


def main(argv: list[str] | None = None) -> None:
    """Run AMOS using Waitress, or Flask's server in explicit debug mode."""
    parser = argparse.ArgumentParser(description="AMOS Simulation Platform Flask App")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=5000, help="Port to bind")
    parser.add_argument("--debug", action="store_true", help="Use Flask development server")
    args = parser.parse_args(argv)

    app = create_app()
    if args.debug:
        app.run(host=args.host, port=args.port, debug=True)
        return

    from waitress import serve

    serve(app, host=args.host, port=args.port, threads=16)


if __name__ == "__main__":
    main()
