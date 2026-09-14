"""Read-only Memcached check. Usage: python3 check_cache_miss.py POST_ID"""

import socket, sys
assert sys.argv[1].isdigit()
with socket.create_connection(("127.0.0.1", 11212), timeout=5) as s:
    s.sendall(("get " + sys.argv[1] + "\r\n").encode())
    with s.makefile("rb") as response:
        assert response.readline() == b"END\r\n", "Post is already cached"
print("Post is not cached")
