"""
Start the Three-Way Match application.

    python run.py                 -> http://127.0.0.1:5000
    python run.py --port 8080
    python run.py --host 0.0.0.0  -> reachable from other machines on your network
"""
import argparse
import threading
import webbrowser

from app import create_app


def main():
    parser = argparse.ArgumentParser(description="Three-Way Match (PO / GRN / Invoice)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab automatically")
    args = parser.parse_args()

    app = create_app()
    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}"
    print(f"\n  Three-Way Match is running at {url}\n  Default login: admin / admin123 (change it after first sign-in)\n  Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    try:
        from waitress import serve  # production-grade server if installed
        if not args.debug:
            serve(app, host=args.host, port=args.port)
            return
    except ImportError:
        pass
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)


if __name__ == "__main__":
    main()
