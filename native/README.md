# Native (C++) modules

Reserved for performance-critical / hardening code from the design doc (fast hashing,
file watching, reward validation). Planned binding: **pybind11** built with CMake.

Not yet set up — this machine has no C++ toolchain. To enable:

1. Install "Desktop development with C++" via Visual Studio Build Tools
2. Install CMake (`winget install Kitware.CMake`)
3. `backend/.venv/Scripts/pip install pybind11 scikit-build-core`

Until then, the Python backend uses pure-Python equivalents (hashlib, watchdog).
