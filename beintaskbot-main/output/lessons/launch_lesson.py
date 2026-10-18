"""Open the standalone lesson on loopback without browser file:// restrictions.
Double-click this Python file where Python file association is available, or run:
python launch_lesson.py
Stop with Ctrl+C. No dependencies, remote listeners or production access.
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import webbrowser

def main():
    folder=Path(__file__).resolve().parent
    with ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(folder))) as server:
        url=f'http://127.0.0.1:{server.server_port}/beinsystems-warehouse-transfer-az.html'
        print(url,flush=True)
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

if __name__=='__main__':
    main()
