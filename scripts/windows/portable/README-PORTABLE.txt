chasingclaw Windows portable package
===================================

This package can run without installing Python.

1) One-click start
------------------
Double-click: chasingclaw-portable-ui.bat

2) Manage process
-----------------
chasingclaw-portable-ui.bat status
chasingclaw-portable-ui.bat stop
chasingclaw-portable-ui.bat restart

3) Access URL
-------------
http://localhost:18789

4) Logs
-------
logs/chasingclaw-ui.out.log
logs/chasingclaw-ui.err.log

5) Notes
--------
- Keep this folder writable.
- Runtime data is stored in: %USERPROFILE%\\.chasingclaw
- If port 18789 is occupied, run:
  chasingclaw-portable-ui.bat restart -Port 18889
