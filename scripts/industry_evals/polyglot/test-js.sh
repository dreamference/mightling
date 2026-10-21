#!/bin/bash
# JavaScript exercises: the shared node_modules, every skipped test turned on, then the tests.
set -e
[ -e node_modules ] || ln -s /npm-install/node_modules .
[ -e package-lock.json ] || ln -s /npm-install/package-lock.json .
sed -i 's/\bxtest(/test(/g' ./*.spec.js
npm run test --silent
