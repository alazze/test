"""Start the private dashboard on this computer and open its browser."""
import sys,threading,webbrowser
from http.server import ThreadingHTTPServer
from server import SecureHandler


def main():
    port=8765
    try:
        server=ThreadingHTTPServer(('127.0.0.1',port),SecureHandler)
    except OSError:
        print('Port 8765 is already in use. Close the previous dashboard or start with: python app.py serve --port 8766')
        return 1
    print('Dashboard: http://localhost:8765',flush=True)
    print('Keep this window open. Press Ctrl+C to stop.',flush=True)
    threading.Timer(1,lambda:webbrowser.open('http://localhost:8765')).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
    return 0


if __name__=='__main__':sys.exit(main())
