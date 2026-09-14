"""Wait for the NGINX, Memcached, and Jaeger forwards; send no application requests."""

import socket, time
for port in (18080, 11212, 16686):
    for attempt in range(30):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                break
        except OSError:
            time.sleep(1)
    else:
        raise SystemExit("Port forward unavailable: " + str(port))
