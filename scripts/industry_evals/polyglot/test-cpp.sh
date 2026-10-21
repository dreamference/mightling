#!/bin/bash
# C++ exercises: configure with every test on, then build (the build runs the tests).
set -e
mkdir -p build
cd build
cmake -DEXERCISM_RUN_ALL_TESTS=1 -G "Unix Makefiles" ..
make
