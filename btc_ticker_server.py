#!/usr/bin/env python3
"""Tiny HTTP server to serve btc_ticker.html on localhost (avoids CORS issues with file://)"""
import http.server, os, webbrowser, signal, sys

PORT = 8777
os.chdir(os.path.dirname(os.path.abspath(__file__)))
signal.signal(signal.SIGINT, lambda *a: sys.exit(0))

handler = http.server.SimpleHTTPRequestHandler
handler.extensions_map.update({".html": "text/html", ".js": "application/javascript"})

httpd = http.server.HTTPServer(("127.0.0.1", PORT), handler)
print(f"BTC Ticker running at http://127.0.0.1:{PORT}/btc_ticker.html")
webbrowser.open(f"http://127.0.0.1:{PORT}/btc_ticker.html")
httpd.serve_forever()
